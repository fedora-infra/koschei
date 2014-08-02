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

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(logging.StreamHandler(sys.stderr))

config = None
if os.path.exists('config.cfg'):
    config_path = 'config.cfg'
else:
    config_path = '/etc/koschei/config.cfg'
with open(config_path) as config_file:
    code = compile(config_file.read(), config_path, 'exec')
    exec(code)

log = logging.getLogger('koschei')

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

class PackageBlocked(Exception):
    pass

def create_koji_session(anonymous=False):
    koji_session = koji.ClientSession(server, {'timeout': 3600})
    if not anonymous:
        koji_session.ssl_login(cert, ca_cert, ca_cert)
    return koji_session

def koji_scratch_build(session, name, opts=None):
    build_opts = base_build_opts.copy()
    if opts:
        build_opts.update(opts)
    build_opts['scratch'] = True
    source = '{}/{}?#{}'.format(scm_url, name, git_reference)

    info = session.listTagged(source_tag, latest=True, package=name)
    if len(info) > 0:
        srpms = session.listRPMs(buildID=info[0]['build_id'], arches='src')
        if len(srpms) > 0:
            source = rel_pathinfo.build(info[0]) + '/' + rel_pathinfo.rpm(srpms[0])
    else:
        pkg_id = session.getPackageID(name)
        [pkg_info] = session.listPackages(pkgID=pkg_id)
        if pkg_info['blocked']:
            raise PackageBlocked()

    log.info('Intiating koji build for {name}:\n\tsource={source}\
              \n\ttarget={target}\n\tbuild_opts={build_opts}'.format(name=name,
                  target=target_tag, source=source, build_opts=build_opts))
    task_id = session.build(source, target_tag, build_opts,
                            priority=koji_config['task_priority'])
    log.info('Submitted koji scratch build for {name}, task_id={task_id}'\
              .format(name=name, task_id=task_id))
    return task_id

def download_task_output(session, task_id, output_dir, filename_predicate=None,
                         prefix_task_id=False):
    main_task = session.getTaskInfo(task_id)
    subtasks = session.getTaskChildren(task_id)
    for task in subtasks + [main_task]:
        files = session.listTaskOutput(task['id'])
        downloads = filter(filename_predicate, files)
        for download in downloads:
            file_name = download
            if prefix_task_id:
                file_name = '{}-{}'.format(task['id'], download)
            log.info('Downloading {} (task_id={}) to {}'\
                      .format(file_name, task['id'], output_dir))
            with open(os.path.join(output_dir, file_name), 'w') as new_file:
                new_file.write(session.downloadTaskOutput(task['id'], download))

def mkdir_if_absent(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def reset_sigpipe():
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

def get_srpm(url, srpm_name):
    url += '/' + srpm_name
    mkdir_if_absent(srpm_dir)
    srpm_path = os.path.join(srpm_dir, os.path.basename(srpm_name))
    if not os.path.isfile(srpm_path):
        tmp_filename = os.path.join(srpm_dir, '.srpm.tmp')
        log.info('downloading {}'.format(srpm_name))
        cmd = 'curl -s {} | tee {} | rpm -qp /dev/fd/0'.format(url, tmp_filename)
        subprocess.call(['bash', '-e', '-c', cmd], preexec_fn=reset_sigpipe)
        os.rename(tmp_filename, srpm_path)
    return srpm_path

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
            get_srpm(url, srpm_name)
        package_names = package_names[50:]
    log.debug('createrepo_c')
    out = subprocess.check_output(['createrepo_c', srpm_dir])
    log.debug(out)

def get_srpm_repodata():
    h = librepo.Handle()
    h.local = True
    h.repotype = librepo.LR_YUMREPO
    h.urls = [srpm_dir]
    return h.perform(librepo.Result())

def download_koji_repos():
    repos = {}
    for arch, repo_url in config['dependency']['repos'].items():
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
    comps_url = config['dependency']['comps_url']
    group_name = config['dependency']['build_group']
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
    capacity = sum(host['capacity'] for host in hosts)
    load = sum(host['task_load'] if host['ready']
               else host['capacity'] for host in hosts)
    return load / capacity
