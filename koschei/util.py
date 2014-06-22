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
import json
import urllib2 as urllib
import subprocess
import hawkey
import shutil

from lxml import etree

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(logging.StreamHandler(sys.stderr))

if sys.argv[0].startswith('/usr'):
    config_path = '/etc/koschei/config.json'
else:
    config_path = 'config.json'
with open(config_path) as config_file:
    config = json.load(config_file)

log = logging.getLogger('koschei')

koji_config = config['koji_config']
server = koji_config['server']
cert = os.path.expanduser(koji_config['cert'])
ca_cert = os.path.expanduser(koji_config['ca'])

git_reference = config.get('git_reference', 'origin/master')

server_opts = {
}

srpm_dir = 'srpms'
repodata_dir = 'repodata'

def create_koji_session(anonymous=False):
    koji_session = koji.ClientSession(server, server_opts)
    if not anonymous:
        koji_session.ssl_login(cert, ca_cert, ca_cert)
    return koji_session

def koji_scratch_build(session, name):
    build_opts = {
            'scratch': True,
        }
    git_url = 'git://pkgs.fedoraproject.org/{}'.format(name)
    source = '{}?#{}'.format(git_url, git_reference)
    target = 'rawhide'
    log.info('Intiating koji build for {name}:\n\tsource={source}\
              \n\ttarget={target}\n\tbuild_opts={build_opts}'.format(name=name,
                  target=target, source=source, build_opts=build_opts))
    task_id = session.build(source, target, build_opts)
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
    except OSError:
        pass

def get_srpm(url, srpm_name):
    url += '/' + srpm_name
    srpm_path = os.path.join(srpm_dir, os.path.basename(srpm_name))
    if not os.path.isfile(srpm_path):
        tmp_filename = '.srpm.tmp'
        with open(tmp_filename, 'w') as tmp_file:
            log.info('downloading {}'.format(srpm_name))
            srpm_content = urllib.urlopen(url).read()
            tmp_file.write(srpm_content)
        os.rename(tmp_filename, srpm_path)
    return srpm_path

def create_srpm_repo(package_names):
    koji_session = create_koji_session()
    koji_session.multicall = True
    pathinfo = koji.PathInfo(topdir='http://kojipkgs.fedoraproject.org')
    for package_name in package_names:
        koji_session.listTagged('f21', latest=True, package=package_name)
    urls = []
    infos = koji_session.multiCall()
    koji_session.multicall = True
    for [info] in infos:
        koji_session.listRPMs(buildID=info[0]['build_id'], arches='src')
        urls.append(pathinfo.build(info[0]))
    srpms = koji_session.multiCall()
    for [srpm], url in zip(srpms, urls):
        srpm_name = pathinfo.rpm(srpm[0])
        get_srpm(url, srpm_name)
    log.debug('createrepo_c')
    out = subprocess.check_output(['createrepo_c', srpm_dir])
    log.debug(out)

def get_paths_from_repomd(repomd_string, data=('primary', 'filelists')):
    nsmap = {'r': 'http://linux.duke.edu/metadata/repo'}
    repomd = etree.fromstring(repomd_string)
    def repomd_get_file(name):
        link = repomd.xpath('/r:repomd/r:data[@type="{}"]/r:location/@href'\
                            .format(name), namespaces=nsmap)[0]
        return link
    return {name: repomd_get_file(name) for name in data}

def load_repos(sack):
    for i, repo_path in enumerate(config['repos']):
        repoid = 'repo' + str(i)
        repo = hawkey.Repo(repoid)
        repomd_path = repo_path + '/repodata/repomd.xml'
        repomd_string = urllib.urlopen(repomd_path).read()
        repodir = os.path.join(repodata_dir, repoid)
        repodata = os.path.join(repodir, 'repodata')
        if os.path.exists(repodir):
            shutil.rmtree(repodir)
        os.makedirs(repodata)
        repomd_files = get_paths_from_repomd(repomd_string)
        for link in repomd_files.values():
            path = '{}/{}'.format(repo_path, link)
            local = os.path.join(repodir, link)
            with open(local, 'w') as local_file:
                local_file.write(urllib.urlopen(path).read())
        repo.repomd_fn = os.path.join(repodata, 'repomd.xml')
        with open(repo.repomd_fn, 'w') as repomd_file:
            repomd_file.write(repomd_string)
        repo.primary_fn = os.path.join(repodir, repomd_files['primary'])
        repo.filelists_fn = os.path.join(repodir, repomd_files['filelists'])
        sack.load_yum_repo(repo, load_filelists=True)

def create_sack(package_names):
    create_srpm_repo(package_names)
    srpm_repo = hawkey.Repo('srpm')
    repomd_path = os.path.join(srpm_dir, 'repodata', 'repomd.xml')
    with open(repomd_path) as repomd:
        repomd_files = get_paths_from_repomd(repomd.read())
    srpm_repo.repomd_fn = repomd_path
    srpm_repo.primary_fn = os.path.join(srpm_dir, repomd_files['primary'])
    srpm_repo.filelists_fn = os.path.join(srpm_dir, repomd_files['filelists'])
    sack = hawkey.Sack()
    sack.load_yum_repo(srpm_repo, load_filelists=True)
    load_repos(sack)
    return sack
