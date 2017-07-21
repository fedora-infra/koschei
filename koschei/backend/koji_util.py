# Copyright (C) 2014-2016  Red Hat, Inc.
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

from __future__ import print_function, absolute_import, division

import re
import koji
import logging

from rpm import RPMSENSE_LESS, RPMSENSE_GREATER, RPMSENSE_EQUAL

from koschei.config import get_config


class KojiSession(object):
    def __init__(self, koji_id='primary', anonymous=True):
        self.koji_id = koji_id
        self.config = get_config('koji_config' if koji_id == 'primary' else
                                 'secondary_koji_config')
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


def itercall(koji_session, args, koji_call):
    chunk_size = get_config('koji_config.multicall_chunk_size')
    while args:
        koji_session.multicall = True
        for arg in args[:chunk_size]:
            koji_call(koji_session, arg)
        for info in koji_session.multiCall():
            if len(info) == 1:
                yield info[0]
            else:
                yield None
        args = args[chunk_size:]


def prepare_build_opts(opts=None):
    build_opts = get_config('koji_config').get('build_opts', {}).copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    return build_opts


def get_last_srpm(koji_session, tag, name, relative=False):
    rel_pathinfo = koji.PathInfo(topdir=koji_session.config[
        'srpm_relative_path_root' if relative else 'topurl'])
    info = koji_session.listTagged(tag, latest=True,
                                   package=name, inherit=True)
    if info:
        srpms = koji_session.listRPMs(buildID=info[0]['build_id'],
                                      arches='src')
        if srpms:
            return (srpms[0],
                    rel_pathinfo.build(info[0]) + '/' +
                    rel_pathinfo.rpm(srpms[0]))


def koji_scratch_build(session, target, name, source, build_opts):
    assert target or build_opts['repo_id']
    build_opts = prepare_build_opts(build_opts)
    log = logging.getLogger('koschei.backend.koji_util')
    log.info('Intiating koji build for %(name)s:\n\tsource=%(source)s'
             '\n\ttarget=%(target)s\n\tbuild_opts=%(build_opts)s',
             dict(name=name, target=target, source=source,
                  build_opts=build_opts))
    task_id = session.build(source, target, build_opts,
                            priority=get_config('koji_config.task_priority'))
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


def cached_koji_call(fn):
    cache_name = re.sub(r'^get_', '', fn.__name__)

    def decorated(session, koji_session, *args, **kwargs):
        cache = session.cache(cache_name)
        namespace = '{}-{}'.format(cache_name, koji_session.koji_id)

        @cache.cache_on_arguments(namespace=namespace)
        def raw_call(args, kwargs):
            return fn(koji_session, *args, **kwargs)

        return raw_call(args, kwargs)

    return decorated


def get_build_group(koji_session, tag_name, group_name):
    groups = koji_session.getTagGroups(tag_name)
    [packages] = [group['packagelist'] for group in groups if group['name'] == group_name]
    return [package['package'] for package in packages
            if not package['blocked'] and package['type'] in ('default', 'mandatory')]

get_build_group_cached = cached_koji_call(get_build_group)


def get_koji_arches(koji_session, build_tag):
    build_config = koji_session.getBuildConfig(build_tag)
    return build_config['arches'].split()

get_koji_arches_cached = cached_koji_call(get_koji_arches)


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


def get_rpm_requires_cached(session, koji_session, nvras):
    cache = session.cache('rpm_requires')

    @cache.cache_multi_on_arguments(namespace='rpm_requires-' + koji_session.koji_id)
    def get_rpm_requires_inner(*nvras):
        return list(get_rpm_requires(koji_session, nvras))

    return get_rpm_requires_inner(*nvras)


def get_koji_load(koji_session, all_arches, arches):
    assert arches
    noarch = 'noarch' in arches
    if noarch:
        arches = all_arches
    channel = koji_session.getChannel('default')
    hosts = koji_session.listHosts(arches, channel['id'], enabled=True)
    min_load = 1
    max_load = 0
    for arch in set(map(koji.canonArch, arches)):
        arch_hosts = [host for host in hosts if arch in host['arches'].split()]
        capacity = sum(host['capacity'] for host in arch_hosts)
        load = sum(min(host['task_load'], host['capacity']) if host['ready']
                   else host['capacity'] for host in arch_hosts)
        arch_load = load / capacity if capacity else 1.0
        min_load = min(min_load, arch_load)
        max_load = max(max_load, arch_load)
    return min_load if noarch else max_load


def get_srpm_arches(koji_session, all_arches, nvra, arch_override=None):
    # compute arches the same way as koji
    # see kojid/getArchList
    archlist = all_arches
    tag_archlist = [koji.canonArch(a) for a in archlist]
    headers = koji_session.getRPMHeaders(
        rpmID=nvra,
        headers=['BUILDARCHS', 'EXCLUDEARCH', 'EXCLUSIVEARCH'],
    )
    if not headers:
        return None
    buildarchs = headers.get('BUILDARCHS', [])
    exclusivearch = headers.get('EXCLUSIVEARCH', [])
    excludearch = headers.get('EXCLUDEARCH', [])
    if buildarchs:
        archlist = buildarchs
    if exclusivearch:
        archlist = [arch for arch in archlist if arch in exclusivearch]
    if excludearch:
        archlist = [arch for arch in archlist if arch not in excludearch]

    if ('noarch' not in excludearch and
            ('noarch' in buildarchs or 'noarch' in exclusivearch)):
        archlist.append('noarch')

    if arch_override:
        # we also allow inverse overrides
        if arch_override.startswith('^'):
            excluded = {koji.canonArch(arch) for arch in arch_override[1:].split()}
            archlist = [arch for arch in archlist if koji.canonArch(arch) not in excluded]
        else:
            archlist = arch_override.split()

    koschei_arches = get_config('koji_config').get('build_arches')
    allowed_arches = set(tag_archlist) & set(koschei_arches)

    arches = set()
    for arch in archlist:
        if arch == 'noarch' or koji.canonArch(arch) in allowed_arches:
            arches.add(arch)

    return arches


def get_latest_repo(koji_session, build_tag):
    return koji_session.getRepo(build_tag, state=koji.REPO_READY)
