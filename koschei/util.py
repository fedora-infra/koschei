# Copyright (C) 2014  Red Hat, Inc.
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

from __future__ import print_function

import os
import sys
import koji
import logging
import subprocess
import hawkey
import librepo
import shutil
import errno
import libcomps
import urllib2

from datetime import datetime

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(logging.StreamHandler(sys.stderr))

config = None
config_path = os.environ.get('KOSCHEI_CONFIG')
if not config_path:
    if os.path.exists('config.cfg'):
        config_path = 'config.cfg'
    else:
        config_path = '/etc/koschei/config.cfg'
with open(config_path) as config_file:
    code = compile(config_file.read(), config_path, 'exec')
    exec(code)

log = logging.getLogger('koschei.util')

koji_config = config['koji_config']
server = koji_config['server']
cert = os.path.expanduser(koji_config['cert'])
ca_cert = os.path.expanduser(koji_config['ca'])
base_build_opts = koji_config.get('build_opts', {})
pathinfo = koji.PathInfo(topdir=koji_config['topurl'])
rel_pathinfo = koji.PathInfo(topdir='..')
scm_url = koji_config['scm_url']
source_tag = koji_config['source_tag']
target_tag = koji_config['target_tag']

git_reference = config.get('git_reference', 'origin/master')


srpm_dir = config['directories']['srpms']
repodata_dir = config['directories']['repodata']

dep_config = config['dependency']
koji_repos = dep_config['repos']

def parse_koji_time(string):
    return datetime.strptime(string, "%Y-%m-%d %H:%M:%S.%f")

def create_koji_session(anonymous=False):
    koji_session = koji.ClientSession(server, {'timeout': 3600})
    if not anonymous:
        koji_session.ssl_login(cert, ca_cert, ca_cert)
    return koji_session

def prepare_build_opts(opts=None):
    build_opts = base_build_opts.copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    return build_opts

def get_last_srpm(koji_session, name):
    info = koji_session.listTagged(source_tag, latest=True, package=name)
    if info:
        srpms = koji_session.listRPMs(buildID=info[0]['build_id'], arches='src')
        if srpms:
            return (srpms[0],
                    rel_pathinfo.build(info[0]) + '/' + rel_pathinfo.rpm(srpms[0]))

def koji_scratch_build(session, name, source, build_opts):
    build_opts = prepare_build_opts(build_opts)
    log.info('Intiating koji build for {name}:\n\tsource={source}\
              \n\ttarget={target}\n\tbuild_opts={build_opts}'.format(name=name,
                  target=target_tag, source=source, build_opts=build_opts))
    task_id = session.build(source, target_tag, build_opts,
                            priority=koji_config['task_priority'])
    log.info('Submitted koji scratch build for {name}, task_id={task_id}'\
              .format(name=name, task_id=task_id))
    return task_id

def mkdir_if_absent(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def reset_sigpipe():
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def download_rpm_header(url, target_dir):
    mkdir_if_absent(target_dir)
    rpm_path = os.path.join(target_dir, os.path.basename(url))
    if not os.path.isfile(rpm_path):
        tmp_filename = os.path.join(target_dir, '.rpm.tmp')
        log.info('downloading {}'.format(rpm_path))
        cmd = 'curl -s {} | tee {} | rpm -qp /dev/fd/0'.format(url, tmp_filename)
        subprocess.call(['bash', '-e', '-c', cmd], preexec_fn=reset_sigpipe)
        os.rename(tmp_filename, rpm_path)
    return rpm_path

def create_srpm_repo(package_names):
    koji_session = create_koji_session()
    while package_names:
        koji_session.multicall = True
        for package_name in package_names[:50]:
            koji_session.listTagged(source_tag, latest=True, package=package_name)
        urls = []
        infos = koji_session.multiCall()
        koji_session.multicall = True
        for [info] in infos:
            if info:
                koji_session.listRPMs(buildID=info[0]['build_id'], arches='src')
                urls.append(pathinfo.build(info[0]))
        srpms = koji_session.multiCall()
        for [srpm], url in zip(srpms, urls):
            srpm_name = pathinfo.rpm(srpm[0])
            download_rpm_header(url + '/' + srpm_name, srpm_dir)
        package_names = package_names[50:]
    log.debug('createrepo_c')
    createrepo = subprocess.Popen(['createrepo_c', srpm_dir], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
    out, err = createrepo.communicate()
    ret = createrepo.wait()
    if ret:
        raise Exception("Createrepo failed: return code {ret}\n{err}"
                        .format(ret=ret, err=err))
    log.debug(out)

def get_srpm_repodata():
    h = librepo.Handle()
    h.local = True
    h.repotype = librepo.LR_YUMREPO
    h.urls = [srpm_dir]
    return h.perform(librepo.Result())

def download_koji_repos():
    repos = {}
    for arch, repo_url in koji_repos.items():
        h = librepo.Handle()
        h.destdir = os.path.join(repodata_dir, arch)
        if os.path.exists(h.destdir):
            shutil.rmtree(h.destdir)
        os.mkdir(h.destdir)
        h.repotype = librepo.LR_YUMREPO
        h.urls = [repo_url]
        h.yumdlist = ['primary', 'filelists', 'group']
        log.info("Downloading {arch} repo from {url}".format(arch=arch, url=repo_url))
        result = h.perform(librepo.Result())
        repos[arch] = result
    return repos

def load_local_repos():
    repos = {}
    for arch in koji_repos.keys():
        h = librepo.Handle()
        h.local = True
        h.repotype = librepo.LR_YUMREPO
        h.urls = [os.path.join(repodata_dir, arch)]
        h.yumdlist = ['primary', 'filelists', 'group']
        repos[arch] = h.perform(librepo.Result())
    return repos

def add_repo_to_sack(repoid, repo_result, sack):
    repodata = repo_result.yum_repo
    repo = hawkey.Repo(repoid)
    repo.repomd_fn = repodata['repomd']
    repo.primary_fn = repodata['primary']
    repo.filelists_fn = repodata['filelists']
    sack.load_yum_repo(repo, load_filelists=True)

def sync_repos(package_names):
    create_srpm_repo(package_names)
    repos = download_koji_repos()
    arches = repos.keys()
    repos['srpm'] = get_srpm_repodata()
    return arches, repos

def create_sacks(arches, repos):
    sacks = {}
    for arch in arches:
        sack = hawkey.Sack(arch=arch)
        for repo_arch, repo in repos.items():
            add_repo_to_sack(repo_arch, repo, sack)
        sacks[arch] = sack
    return sacks

def get_build_group():
    comps_url = dep_config['comps_url']
    group_name = dep_config['build_group']
    comps = libcomps.Comps()
    comps.fromxml_str(urllib2.urlopen(comps_url).read())
    [group] = [group for group in comps.groups if group.name == group_name]
    return [pkg.name for pkg in group.packages]

def get_koji_packages(package_names):
    session = create_koji_session(anonymous=True)
    session.multicall = True
    for name in package_names:
        session.getPackage(name)
    pkgs = session.multiCall()
    return [pkg for [pkg] in pkgs]

def get_koji_load(koji_session):
    channel = koji_session.getChannel('default')
    hosts = koji_session.listHosts(channelID=channel['id'], enabled=True)
    max_load = 0
    build_arches = koji_config.get('build_arches')
    assert build_arches
    for arch in build_arches:
        arch_hosts = [host for host in hosts if arch in host['arches']]
        capacity = sum(host['capacity'] for host in arch_hosts)
        load = sum(host['task_load'] if host['ready']
               else host['capacity'] for host in arch_hosts)
        max_load = max(max_load, load / capacity if capacity else 1.0)
    return max_load

def download_task_output(koji_session, task_id, file_name, out_path):
    offset = 0
    chunk_size = koji_config.get('chunk_size', 1024 * 1024)
    with open(out_path, 'w') as out_file:
        while True:
            out = koji_session.downloadTaskOutput(task_id, file_name, size=chunk_size,
                                                  offset=offset)
            if not out:
                return
            offset += len(out)
            out_file.write(out)
