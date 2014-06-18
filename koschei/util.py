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
import koji
import logging
import json

# TODO look for it in better place than $PWD
config_path = 'config.json'
with open(config_path) as config_file:
    config = json.load(config_file)

log = logging.getLogger('koschei')

koji_config = config['koji_config']
server = koji_config['server']
cert = os.path.expanduser(koji_config['cert'])
ca_cert = os.path.expanduser(koji_config['ca'])

git_reference = config.get('git_reference', 'origin/master')

dry_run = True

server_opts = {
}

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
    if not dry_run:
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

