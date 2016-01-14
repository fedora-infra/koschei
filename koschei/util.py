# Copyright (C) 2014-2015  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function

import os
import re
import rpm
import koji
import logging
import logging.config
import hawkey
import errno
import fcntl
import time
import socket

from Queue import Queue
from threading import Thread
from contextlib import contextmanager
from rpm import RPMSENSE_LESS, RPMSENSE_GREATER, RPMSENSE_EQUAL


def merge_dict(d1, d2):
    ret = d1.copy()
    for k, v in d2.items():
        if k in ret and isinstance(v, dict) and isinstance(ret[k], dict):
            ret[k] = merge_dict(ret[k], v)
        else:
            ret[k] = v
    return ret


DEFAULT_CONFIGS = '/usr/share/koschei/config.cfg:/etc/koschei/config.cfg'
config = {}


def load_config():
    def parse_config(config_path):
        global config
        if os.path.exists(config_path):
            with open(config_path) as config_file:
                code = compile(config_file.read(), config_path, 'exec')
                conf_locals = {}
                exec code in conf_locals
                if 'config' in conf_locals:
                    config = merge_dict(config, conf_locals['config'])
    config_paths = os.environ.get('KOSCHEI_CONFIG', DEFAULT_CONFIGS)
    for config_path in config_paths.split(':'):
        parse_config(config_path)

load_config()
assert config != {}

logging.config.dictConfig(config['logging'])
log = logging.getLogger('koschei.util')

koji_config = config['koji_config']
server = koji_config['server']
authtype = koji_config['authtype']
cert = os.path.expanduser(koji_config['cert'])
ca_cert = os.path.expanduser(koji_config['ca'])
base_build_opts = koji_config.get('build_opts', {})
pathinfo = koji.PathInfo(topdir=koji_config['topurl'])
rel_pathinfo = koji.PathInfo(topdir='..')
source_tag = koji_config['source_tag']
target_tag = koji_config['target_tag']

git_reference = config.get('git_reference', 'origin/master')


repodata_dir = config['directories']['repodata']

dep_config = config['dependency']


class SessionProxy(object):
    def __init__(self, session_name, constructor):
        self.__constructor = constructor
        self.__proxied = constructor()
        self.__session_name = session_name

    def reset_session(self):
        self.__proxied = self.__constructor()

    def __getattr__(self, name):
        result = getattr(self.__proxied, name)
        if callable(result):
            def decorated(*args, **kwargs):
                retry_in = config.get('base_retry_interval', 10)
                while True:
                    if not self.__proxied:
                        self.reset_session()
                    method = getattr(self.__proxied, name)
                    try:
                        return method(*args, **kwargs)
                    except Exception:
                        log.exception("%s exception. Retrying in %s.",
                                      self.__session_name.capitalize(),
                                      retry_in)
                        self.__proxied = None
                        time.sleep(retry_in)
                        retry_in *= 2
            return decorated
        return result

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self.__proxied, name, value)


class KojiSession(SessionProxy):
    def __init__(self, anonymous=True):
        def constructor():
            koji_session = koji.ClientSession(server, {'timeout': 3600})
            if not anonymous:
                if authtype == 'ssl':
                    koji_session.ssl_login(cert, ca_cert, ca_cert)
                elif authtype == 'kerberos':
                    koji_session.krb_login()
                else:
                    raise RuntimeError("Unsupported Koji authtype: {}".format(authtype))
            return koji_session
        super(KojiSession, self).__init__('koji', constructor)
        self.__mcall_list = []


    def __multi_call(self):
        sup = super(KojiSession, self)
        assert self.multicall
        for name, args, kwargs in self.__mcall_list:
            sup.__getattr__(name)(*args, **kwargs)
        self.__mcall_list = []
        return sup.__getattr__('multiCall')()

    def __getattr__(self, name):
        sup = super(KojiSession, self)
        if name == 'multiCall':
            return self.__multi_call
        result = sup.__getattr__(name)
        if sup.__getattr__('multicall') and callable(result):
            def wrapper(*args, **kwargs):
                self.__mcall_list.append((name, args, kwargs))
            return wrapper
        return result


def itercall(koji_session, args, koji_call):
    chunk_size = koji_config['multicall_chunk_size']
    while args:
        koji_session.multicall = True
        for arg in args[:chunk_size]:
            koji_call(koji_session, arg)
        for [info] in koji_session.multiCall():
            yield info
        args = args[chunk_size:]


class parallel_generator(object):
    sentinel = object()

    def __init__(self, generator, queue_size=1000):
        self.generator = generator
        self.queue = Queue(maxsize=queue_size)
        self.worker_exception = StopIteration
        self.stop_thread = False
        self.thread = Thread(target=self.worker_fn)
        self.thread.daemon = True
        self.thread.start()

    def worker_fn(self):
        try:
            for item in self.generator:
                self.queue.put(item)
                if self.stop_thread:
                    return
        except Exception as e:
            self.worker_exception = e
        finally:
            self.queue.put(self.sentinel)

    def __iter__(self):
        return self

    def next(self):
        item = self.queue.get()
        if item is self.sentinel:
            raise self.worker_exception # StopIteration in case of success
        return item

    def stop(self):
        self.stop_thread = True


def prepare_build_opts(opts=None):
    build_opts = base_build_opts.copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    return build_opts


def get_last_srpm(koji_session, name):
    info = koji_session.listTagged(source_tag, latest=True,
                                   package=name, inherit=True)
    if info:
        srpms = koji_session.listRPMs(buildID=info[0]['build_id'],
                                      arches='src')
        if srpms:
            return (srpms[0],
                    rel_pathinfo.build(info[0]) + '/' +
                    rel_pathinfo.rpm(srpms[0]))


def koji_scratch_build(session, name, source, build_opts):
    build_opts = prepare_build_opts(build_opts)
    log.info('Intiating koji build for %(name)s:\n\tsource=%(source)s\
              \n\ttarget=%(target)s\n\tbuild_opts=%(build_opts)s',
             dict(name=name, target=target_tag, source=source,
                  build_opts=build_opts))
    task_id = session.build(source, target_tag, build_opts,
                            priority=koji_config['task_priority'])
    log.info('Submitted koji scratch build for %s, task_id=%d', name, task_id)
    return task_id


def mkdir_if_absent(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def _get_best_selector(sack, dep):
    # Based on get_best_selector in dnf's subject.py

    def is_glob_pattern(pattern):
        return set(pattern) & set("*[?")

    def first(iterable):
        it = iter(iterable)
        try:
            return next(it)
        except StopIteration:
            return None

    def _nevra_to_selector(sltr, nevra):
        if nevra.name is not None:
            if is_glob_pattern(nevra.name):
                sltr.set(name__glob=nevra.name)
            else:
                sltr.set(name=nevra.name)
        if nevra.version is not None:
            evr = nevra.version
            if nevra.epoch is not None and nevra.epoch > 0:
                evr = "%d:%s" % (nevra.epoch, evr)
            if nevra.release is None:
                sltr.set(version=evr)
            else:
                evr = "%s-%s" % (evr, nevra.release)
                sltr.set(evr=evr)
        if nevra.arch is not None:
            sltr.set(arch=nevra.arch)
        return sltr

    subj = hawkey.Subject(dep)
    sltr = hawkey.Selector(sack)
    kwargs = {'allow_globs': True}
    if re.search(r'^\*?/', dep):
        key = "file__glob" if is_glob_pattern(dep) else "file"
        return sltr.set(**{key: dep})
    nevra = first(subj.nevra_possibilities_real(sack, **kwargs))
    if nevra:
        return _nevra_to_selector(sltr, nevra)

    if is_glob_pattern(dep):
        return sltr.set(provides__glob=dep)

    # pylint: disable=E1101
    reldep = first(subj.reldep_possibilities_real(sack))
    if reldep:
        dep = str(reldep)
        return sltr.set(provides=dep)
    return sltr


def run_goal(sack, group, br):
    # pylint:disable=E1101
    goal = hawkey.Goal(sack)
    problems = []
    for name in group:
        sltr = _get_best_selector(sack, name)
        if not sltr.matches():
            problems.append("Package in base build group not found: {}".format(name))
        goal.install(select=sltr)
    for r in br:
        sltr = _get_best_selector(sack, r)
        # pylint: disable=E1103
        if not sltr.matches():
            problems.append("No package found for: {}".format(r))
        else:
            goal.install(select=sltr)
    if not problems:
        kwargs = {}
        if config['dependency']['ignore_weak_deps']:
            kwargs = {'ignore_weak_deps': True}
        resolved = goal.run(**kwargs)
        return resolved, goal.problems, goal.list_installs() if resolved else None
    return False, problems, None


def compute_dependency_distances(sack, br, deps):
    dep_map = {dep.name: dep for dep in deps}
    visited = set()
    level = 1
    # pylint:disable=E1103
    pkgs_on_level = {x for r in br for x in
                     _get_best_selector(sack, r).matches()}
    while pkgs_on_level:
        for pkg in pkgs_on_level:
            dep = dep_map.get(pkg.name)
            if dep and dep.distance is None:
                dep.distance = level
        level += 1
        if level >= 5:
            break
        reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                   for req in pkg.requires}
        visited.update(pkgs_on_level)
        pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))


def get_build_group(koji_session):
    tag_name = koji_config['build_tag']
    group_name = dep_config['build_group']
    groups = koji_session.getTagGroups(tag_name)
    [packages] = [group['packagelist'] for group in groups if group['name'] == group_name]
    return [package['package'] for package in packages
            if not package['blocked'] and package['type'] == 'default']


def get_rpm_requires(koji_session, nvras):
    deps_list = itercall(koji_session, nvras,
                         lambda k, nvra: k.getRPMDeps(nvra, koji.DEP_REQUIRE))
    for deps in deps_list:
        requires = []
        for dep in deps:
            flags = dep['flags']
            if flags & ~(RPMSENSE_LESS | RPMSENSE_GREATER | RPMSENSE_EQUAL):
                continue
            order = ""
            while flags:
                old = flags
                flags &= flags - 1
                order += {RPMSENSE_LESS: '<',
                          RPMSENSE_GREATER: '>',
                          RPMSENSE_EQUAL: '='}[old ^ flags]
            requires.append(("%s %s %s" % (dep['name'], order, dep['version'])).rstrip())
        yield requires


def get_koji_load(koji_session):
    channel = koji_session.getChannel('default')
    build_arches = koji_config.get('build_arches')
    hosts = koji_session.listHosts(build_arches, channel['id'], enabled=True)
    max_load = 0
    assert build_arches
    for arch in build_arches:
        arch_hosts = [host for host in hosts if arch in host['arches']]
        capacity = sum(host['capacity'] for host in arch_hosts)
        load = sum(min(host['task_load'], host['capacity']) if host['ready']
                   else host['capacity'] for host in arch_hosts)
        max_load = max(max_load, load / capacity if capacity else 1.0)
    return max_load


def download_task_output(koji_session, task_id, file_name, out_path):
    offset = 0
    chunk_size = koji_config.get('chunk_size', 1024 * 1024)
    with open(out_path, 'w') as out_file:
        while True:
            out = koji_session.downloadTaskOutput(task_id, file_name,
                                                  size=chunk_size,
                                                  offset=offset)
            if not out:
                return
            offset += len(out)
            out_file.write(out)


def epoch_to_str(epoch):
    return str(epoch) if epoch is not None else None


def compare_evr(evr1, evr2):
    evr1, evr2 = ((epoch_to_str(e), v, r) for (e, v, r) in (evr1, evr2))
    return rpm.labelCompare(evr1, evr2)


def set_difference(s1, s2, key):
    compset = {key(x) for x in s2}
    return {x for x in s1 if key(x) not in compset}


@contextmanager
def lock(lock_path):
    with open(lock_path, 'a+') as lock_file:
        fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def get_latest_repo(koji_session):
    build_tag = koji_config['build_tag']
    return koji_session.getRepo(build_tag, state=koji.REPO_READY)


# Utility class for time measurement
class Stopwatch(object):
    def __init__(self, name, parent=None, start=False):
        self._time = 0
        self._name = name
        self._started = False
        self._children = []
        self._parent = parent
        if parent:
            parent._children.append(self)
        if start:
            self.start()

    def start(self):
        assert not self._started
        if self._parent and not self._parent._started:
            self._parent.start()
        self._time = self._time - time.time()
        self._started = True

    def stop(self):
        assert self._started
        for child in self._children:
            if child._started:
                child.stop()
        self._time = self._time + time.time()
        self._started = False

    def reset(self):
        self._started = False
        for child in self._children:
            child.reset()
        self._time = 0

    def display(self):
        assert not self._started

        def human_readable_time(t):
            s = str(t % 60) + " s"
            t = int(t / 60)
            if t:
                s = str(t % 60) + " min " + s
            t = int(t / 60)
            if t:
                s = str(t % 60) + " h " + s
            return s

        log.debug('{} time: {}'.format(self._name, human_readable_time(self._time)))

        for child in self._children:
            child.display()

def sd_notify(msg):
    sock_path = os.environ.get('NOTIFY_SOCKET', None)
    if not sock_path:
        raise RuntimeError("NOTIFY_SOCKET not set")
    if sock_path[0] == '@':
        sock_path = '\0' + sock_path[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg, sock_path)
    finally:
        sock.close()
