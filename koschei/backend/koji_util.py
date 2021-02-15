# Copyright (C) 2014-2017  Red Hat, Inc.
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

"""
Ac ollection of utility functions and classes for insteacting with Koji.
"""

import re
import koji
import logging

from functools import total_ordering
from rpm import (
    RPMSENSE_LESS, RPMSENSE_GREATER, RPMSENSE_EQUAL,
    RPMSENSE_FIND_REQUIRES
)

from koschei.config import get_config, get_koji_config


class KojiSession(object):
    """
    Koschei's wrapper around koji.ClientSession.
    All koji method calls are passed to the underlying session.
    Adds additional `config` attribute, which contains the current Koschei configuration
    keys for this session (`koji_config` or `secondary_koji_config`).
    Also adds `koji_id` attribute which specifies whther this is primary or secondary
    session.
    """
    def __init__(self, koji_id='primary', anonymous=True):
        """
        :param koji_id: either 'primary' or 'secondary'
        :param anonymous: whether to skip authentication
        """
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


def itercall(koji_session, args, koji_call, chunk_size=None):
    """
    Function that simplifies handling large multicalls, which would normally timeout when
    accessing too much data at once. Splits the arguments into chunks and performs
    multiple multicalls on them.

    The usage:
    ```
    for task_info in itercall(koji_session, [1, 2, 3], lambda k, t: k.getTaskInfo(t)):
        print(task_info['id'])
    ```

    :param koji_session: The koji session used to make the multicalls
    :param args: A list of arguments that will be individually passed to `koji_call`
    :param koji_call: A function taking (koji_session, arg) arguments, where `arg` is a
                      single element from `args`. The function should call a single
                      Koji method call.
    :param chunk_size: How many args should go into a single chunk.
    :return: Generator of results from the individual koji method calls
    """
    if not chunk_size:
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
    """
    Prepare build options for a scratch-build.

    :param opts: Additional options to be added.
    :return: A dictionary ready to be passed to `build_opts` argument of Koji's build call
    """
    build_opts = get_config('koji_config').get('build_opts', {}).copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    return build_opts


def get_last_srpm(koji_session, tag, name, relative=False, topdir=None):
    """
    Obtain data for latest SRPM of a package to be used for submitting a build.
    Returns SRPM info and URL pointing to it. May return None if no SRPM was found.

    :param koji_session: Koji session used for queries
    :param tag: Koji build tag name
    :param name: Package name
    :param relative: Whether the URL should be relative to Koji's work dir. Used for
                     submitting scratch-builds from SRPMS existing in the same Koji.
    :param topdir: Alternative Koji topdir, defaults to one supplied in configuration.
    :return: a tuple of (srpm_info, srpm_url) or None.
             srpm_info is Koji's rpm info dictionary, contains 'epoch', 'version',
             'release' fields (and more)
             srpm_url is the URL pointing to the SRPM. May be relative if `relative` is
             specified
    """
    if not topdir:
        topdir = koji_session.config[
            'srpm_relative_path_root' if relative else 'topurl']
    rel_pathinfo = koji.PathInfo(topdir=topdir)
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
    """
    Submit a Koji scratch build.

    :param session: Koji session used for submitting the build
    :param target: Koji target name
    :param name: Package name (used for logging only)
    :param source: build's source URL, typically obtained by
                   get_last_srpm(..., relative=True)
    :param build_opts: additional build options. Default options (scratch=True) and
                       options specified by configuration will be added automatically
    :return: Koji task ID of the new build
    """
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
    Koji fault means a build failure caused by Koji itself (and not the package), for
    example faulure due to a network error.
    """
    try:
        session.getTaskResult(task_id)
        return False
    except koji.LockError:
        return True
    except koji.GenericError:
        return False
    except koji.Fault:
        return True


def cached_koji_call(fn):
    """
    Decorator that adds caching to a function that takes a Koji session. Decorated
    function takes one more argument - the Koschei session.
    Cache provider is chosen based on the `caching` configuration key. The name of the
    subkey is constructed by removing `get_` prefix from the function name.

    :param fn: a function that takes Koji session as first argument
    :return: a function that caches calls of `fn`. Takes KoscheiSession as a first
             argument, then the same arguments as `fn`.
    """
    cache_name = re.sub(r'^get_', '', fn.__name__)

    def decorated(session, koji_session, *args, **kwargs):
        cache = session.cache(cache_name)
        namespace = '{}-{}'.format(cache_name, koji_session.koji_id)

        @cache.cache_on_arguments(namespace=namespace)
        def raw_call(args, kwargs):
            return fn(koji_session, *args, **kwargs)

        return raw_call(args, kwargs)

    return decorated


def get_build_group(koji_session, tag_name, group_name, repo_id):
    """
    Obtains a list of packages from given build group that should be installed by default.

    :param koji_session: Koji session to be used for the query.
    :param tag_name: Name of the queried build tag
    :param group_name: Name of the group, typically "build"
    :param repo_id: Koji repo ID for which the group should be queried. Koji build groups
                    change in time, this ensures we get the one for the repo being
                    resolved.
    :return: List of package names (may be provides). May return None when the group is
             no longer available.
    """
    repo_info = koji_session.repoInfo(repo_id)
    if not repo_info:
        return None
    groups = koji_session.getTagGroups(tag_name, event=repo_info['create_event'])
    if not groups:
        return None
    groups = [group['packagelist'] for group in groups if group['name'] == group_name]
    if not groups:
        return None
    return [
        package['package'] for package in groups[0]
        if not package['blocked'] and package['type'] in ('default', 'mandatory')
    ]


get_build_group_cached = cached_koji_call(get_build_group)


def get_koji_arches(koji_session, build_tag):
    """
    Obtain list of arches used for building in given Koji.

    :param koji_session: Koji session to be used for the query.
    :param build_tag: Koji build tag name
    :return: List of arch names
    """
    build_config = koji_session.getBuildConfig(build_tag)
    return build_config['arches'].split()


get_koji_arches_cached = cached_koji_call(get_koji_arches)


def get_rpm_requires(koji_session, nvras, chunk_size=None):
    """
    Obtain BuildRequires of given packages (NVRAs). Queried in bulk for performance
    reasons.

    :param koji_session: Koji session to be used for the query
    :param nvras: List of NVRA dictionaries for the SRPMs
    :param chunk_size: Passed to `itercall`
    :return: A generator yielding a list of BuildRequires for each package
    """
    deps_list = itercall(koji_session, nvras,
                         lambda k, nvra: k.getRPMDeps(nvra, koji.DEP_REQUIRE),
                         chunk_size=chunk_size)
    for deps in deps_list:
        requires = []
        for dep in deps:
            flags = dep['flags']
            flags &= ~RPMSENSE_FIND_REQUIRES
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
    """
    Cached version of `get_rpm_requires`. Additionally takes Koschei session argument.
    """
    cache = session.cache('rpm_requires')

    @cache.cache_multi_on_arguments(namespace='rpm_requires-' + koji_session.koji_id)
    def get_rpm_requires_inner(*nvras):
        return list(get_rpm_requires(koji_session, nvras))

    return get_rpm_requires_inner(*nvras)


def get_koji_load(koji_session, all_arches, arches):
    """
    Compute load of Koji instance.

    :param koji_session: Koji session to be used for the query
    :param all_arches: List of all arches obtained from `get_koji_arches`
    :param arches: Set of arches for package computed by `get_srpm_arches`
    :return: A floating point number from 0 to 1 representing the load
    """
    assert arches
    noarch = 'noarch' in arches
    if noarch:
        arches = all_arches
    channel = koji_session.getChannel('default')
    hosts = koji_session.listHosts(list(arches), channel['id'], enabled=True)
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


def get_srpm_arches(koji_session, all_arches, nvra, arch_override=None,
                    build_arches=None):
    """
    Compute architectures that should be used for a build. Computation is based on the one
    in Koji (kojid/getArchList).

    :param koji_session: Koji session to be used for the query
    :param all_arches: List of all arches obtained from `get_koji_arches`
    :param nvra: NVRA dict of the SRPM
    :param arch_override: User specified arch override
    :param build_arches: List of allowed arches for building. Taken from config by default
    :return: Set of architectures that can be passed to `koji_scratch_build`. May be
             empty, in which case no build should be submitted.
    """
    archlist = all_arches
    tag_archlist = {koji.canonArch(a) for a in archlist}
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

    if not build_arches:
        build_arches = get_config('koji_config').get('build_arches')
    build_arches = {koji.canonArch(arch) for arch in build_arches}
    allowed_arches = tag_archlist & build_arches

    arches = set()
    for arch in archlist:
        if arch == 'noarch' or koji.canonArch(arch) in allowed_arches:
            arches.add(arch)

    return arches


def get_latest_repo(koji_session, build_tag):
    """
    Returns latest Koji repoInfo for given build tag.
    """
    return koji_session.getRepo(build_tag, state=koji.REPO_READY)


@total_ordering
class KojiRepoDescriptor(object):
    """
    RepoDescriptor used by repo_cache to obtain repos.
    """
    def __init__(self, koji_id, build_tag, repo_id):
        self.koji_id = koji_id
        self.build_tag = build_tag
        self.repo_id = repo_id

    @staticmethod
    def from_string(name):
        """
        Parse KojiRepoDescriptor from filename.
        """
        parts = name.split('-')
        if len(parts) < 3 or not parts[-1].isdigit():
            return None
        return KojiRepoDescriptor(parts[0], '-'.join(parts[1:-1]), int(parts[-1]))

    def __str__(self):
        return '{}-{}-{}'.format(self.koji_id, self.build_tag, self.repo_id)

    def __hash__(self):
        return hash((self.koji_id, self.build_tag, self.repo_id))

    def __eq__(self, other):
        try:
            return (self.koji_id == other.koji_id and
                    self.build_tag == other.build_tag and
                    self.repo_id == other.repo_id)
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.repo_id < other.repo_id

    @property
    def url(self):
        """
        Produce URL where the repo can be downloaded.
        """
        arch = get_config('dependency.repo_arch')
        topurl = get_koji_config(self.koji_id, 'topurl')
        url = '{topurl}/repos/{build_tag}/{repo_id}/{arch}'
        return url.format(topurl=topurl, build_tag=self.build_tag,
                          repo_id=self.repo_id, arch=arch)


def create_repo_descriptor(koji_session, repo_id):
    """
    Create a RepoDescriptor for fetching given repo via repo_cache.

    :param koji_session: Koji session to be used for the query
    :param repo_id: Koji repo ID
    :return: KojiRepoDescriptor for the repo or None if the repo is not available
    """
    valid_repo_states = (koji.REPO_STATES['READY'], koji.REPO_STATES['EXPIRED'])

    repo_info = koji_session.repoInfo(repo_id)
    if repo_info and repo_info.get('state') in valid_repo_states:
        return KojiRepoDescriptor(
            koji_id=koji_session.koji_id,
            build_tag=repo_info['tag_name'],
            repo_id=repo_id,
        )
