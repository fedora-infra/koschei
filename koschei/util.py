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
from rpm import labelCompare, RPMSENSE_LESS, RPMSENSE_GREATER, RPMSENSE_EQUAL


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

primary_koji_config = config['koji_config']
secondary_koji_config = dict(primary_koji_config)
secondary_koji_config.update(config['secondary_koji_config'])
koji_configs = {
    'primary': primary_koji_config,
    'secondary': secondary_koji_config,
}

secondary_mode = bool(config['secondary_koji_config'])


class KojiSession(object):
    def __init__(self, koji_id='primary', anonymous=True):
        self.koji_id = koji_id
        self.config = koji_configs[koji_id]
        self.__anonymous = anonymous
        self.__proxied = self.__new_session()

    def __new_session(self):
        server = self.config['server']
        opts = {
            'anon_retry': True,
            'max_retries': 1000,
            'offline_retry': True,
            'offline_retry_interval': 120,
            'timeout': 3600,
        }
        opts.update(self.config.get('session_opts', {}))
        session = koji.ClientSession(server, opts)
        if not self.__anonymous:
            getattr(session, self.config['login_method'])(**self.config['login_args'])
        return session

    def __getattr__(self, name):
        return getattr(self.__proxied, name)

    def __setattr__(self, name, value):
        if name.startswith('_') or name in ('config', 'koji_id'):
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self.__proxied, name, value)


def chunks(seq, chunk_size=100):
    while seq:
        yield seq[:chunk_size]
        seq = seq[chunk_size:]


def itercall(koji_session, args, koji_call):
    # TODO
    chunk_size = primary_koji_config['multicall_chunk_size']
    while args:
        koji_session.multicall = True
        for arg in args[:chunk_size]:
            koji_call(koji_session, arg)
        for [info] in koji_session.multiCall():
            yield info
        args = args[chunk_size:]


def selective_itercall(session_provider, args, koji_call):
    """
    Version of itercall that is able to use multiple Koji sessions and return
    results in the same order as inputs while using minimal amount of multicalls

    :param session_provider: function that is called on each argument to
    determine Koji session to use
    :param args: list of items to be processed by the call
    :param koji_call: function that gets a Koji session and single item and
    performs a Koji call on it
    :returns: list of multicall results
    """
    sessions = map(session_provider, args)
    results = [None] * len(args)
    for session in set(sessions):
        items = []
        indices = []
        for i, item in enumerate(args):
            if sessions[i] is session:
                items.append(item)
                indices.append(i)
        for i, item in zip(indices, itercall(session, items, koji_call)):
            results[i] = item
    return results


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
            raise self.worker_exception  # StopIteration in case of success
        return item

    def stop(self):
        self.stop_thread = True


def prepare_build_opts(opts=None):
    build_opts = primary_koji_config.get('build_opts', {}).copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    return build_opts


def get_last_srpm(koji_session, tag, name):
    rel_pathinfo = koji.PathInfo(topdir=primary_koji_config['srpm_relative_path_root'])
    info = koji_session.listTagged(tag, latest=True,
                                   package=name, inherit=True)
    if info:
        srpms = koji_session.listRPMs(buildID=info[0]['build_id'],
                                      arches='src')
        if srpms:
            return (srpms[0],
                    rel_pathinfo.build(info[0]) + '/' +
                    rel_pathinfo.rpm(srpms[0]))


def koji_scratch_build(session, target_tag, name, source, build_opts):
    build_opts = prepare_build_opts(build_opts)
    log.info('Intiating koji build for %(name)s:\n\tsource=%(source)s\
              \n\ttarget=%(target)s\n\tbuild_opts=%(build_opts)s',
             dict(name=name, target=target_tag, source=source,
                  build_opts=build_opts))
    task_id = session.build(source, target_tag, build_opts,
                            priority=primary_koji_config['task_priority'])
    log.info('Submitted koji scratch build for %s, task_id=%d', name, task_id)
    return task_id


def is_koji_fault(session, task_id):
    """
    Return true iff specified finished Koji task was ended due to Koji fault.
    """
    try:
        session.getTaskResult(task_id)
        return False
    except koji.GenericError:
        return False
    except koji.Fault:
        return True


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

    nevra = first(subj.nevra_possibilities_real(sack, allow_globs=True))
    if nevra:
        s = _nevra_to_selector(sltr, nevra)
        if len(s.matches()) > 0:
            return s

    # pylint: disable=E1101
    reldep = first(subj.reldep_possibilities_real(sack))
    if reldep:
        dep = str(reldep)
        s = sltr.set(provides=dep)
        if len(s.matches()) > 0:
            return s

    if re.search(r'^\*?/', dep):
        key = "file__glob" if is_glob_pattern(dep) else "file"
        return sltr.set(**{key: dep})

    return sltr


def run_goal(sack, br, group):
    # pylint:disable=E1101
    goal = hawkey.Goal(sack)
    problems = []
    for name in group:
        sltr = _get_best_selector(sack, name)
        # missing packages are silently skipped as in dnf
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


def get_build_group(koji_session, tag_name, group_name):
    groups = koji_session.getTagGroups(tag_name)
    [packages] = [group['packagelist'] for group in groups if group['name'] == group_name]
    return [package['package'] for package in packages
            if not package['blocked'] and package['type'] in ('default', 'mandatory')]


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
    build_arches = primary_koji_config.get('build_arches')
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
    # TODO
    chunk_size = primary_koji_config.get('chunk_size', 1024 * 1024)
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
    return labelCompare(evr1, evr2)


def set_difference(s1, s2, key):
    compset = {key(x) for x in s2}
    return {x for x in s1 if key(x) not in compset}


@contextmanager
def lock(lock_path):
    with open(lock_path, 'a+') as lock_file:
        fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX)
        yield


def get_latest_repo(koji_session, build_tag):
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
