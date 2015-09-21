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
import rpm
import koji
import logging
import logging.config
import hawkey
import errno
import fcntl

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


class KojiException(Exception):
    def __init__(self, cause):
        self.cause = cause
        message = '{}: {}'.format(type(cause), cause)
        super(KojiException, self).__init__(message)


def koji_exception_rewrap_decorator(fn):
    def decorated(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise KojiException(e)
    return decorated


class KojiSessionProxy(object):
    def __init__(self, proxied):
        self.proxied = proxied

    def __getattribute__(self, name):
        proxied = object.__getattribute__(self, 'proxied')
        if name == 'proxied':
            return proxied
        result = getattr(proxied, name)
        if callable(result):
            return koji_exception_rewrap_decorator(result)
        return result

    def __setattr__(self, name, value):
        if name == 'proxied':
            object.__setattr__(self, name, value)
        setattr(self.proxied, name, value)


def itercall(koji_session, args, koji_call):
    chunk_size = koji_config['multicall_chunk_size']
    while args:
        koji_session.multicall = True
        for arg in args[:chunk_size]:
            koji_call(koji_session, arg)
        for [info] in koji_session.multiCall():
            yield info
        args = args[chunk_size:]


def create_koji_session(anonymous=False):
    try:
        koji_session = koji.ClientSession(server, {'timeout': 3600})
        if not anonymous:
            koji_session.ssl_login(cert, ca_cert, ca_cert)
        return koji_session
    except Exception as e:
        raise KojiException(e)


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


def add_repo_to_sack(repoid, repo_result, sack):
    repodata = repo_result.yum_repo
    repo = hawkey.Repo(repoid)
    repo.repomd_fn = repodata['repomd']
    repo.primary_fn = repodata['primary']
    repo.filelists_fn = repodata['filelists']
    sack.load_yum_repo(repo, load_filelists=True)


def add_repos_to_sack(repo_id, repo_results, sack):
    for arch, repo_result in repo_results.items():
        add_repo_to_sack('{}-{}'.format(repo_id, arch), repo_result, sack)


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
    requires_list = []
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
        requires_list.append(requires)
    return requires_list


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
