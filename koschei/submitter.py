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

import logging
import koji
import os

from datetime import datetime

from koschei import util
from koschei.models import Build
from koschei.plugins import dispatch_event

log = logging.getLogger('submitter')

log_output_dir = util.config['directories']['build_logs']

def submit_builds(db_session, koji_session):
    scheduled_builds = db_session.query(Build).filter_by(state=Build.SCHEDULED)
    for build in scheduled_builds:
        name = build.package.name
        build.state = Build.RUNNING
        build.task_id = util.koji_scratch_build(koji_session, name)
        build.started = datetime.now()
        build.package.manual_priority = 0
        db_session.commit()
        dispatch_event('build_submitted', db_session, build)

def poll_tasks(db_session, koji_session):
    running_builds = db_session.query(Build).filter_by(state=Build.RUNNING)
    for build in running_builds:
        name = build.package.name
        if not build.task_id:
            log.warn('No task id assigned to build {0})'.format(build))
        else:
            task_info = koji_session.getTaskInfo(build.task_id)
            log.debug('Polling task {id} ({name}): task_info={info}'\
                      .format(id=build.task_id, name=name, info=task_info))
            state = koji.TASK_STATES.getvalue(task_info['state'])
            update_koji_state(db_session, build, state)

def update_koji_state(db_session, build, state):
    if state in Build.KOJI_STATE_MAP:
        state = Build.KOJI_STATE_MAP[state]
        log.info('Setting build {build} state to {state}'\
                  .format(build=build, state=Build.REV_STATE_MAP[state]))
        build.state = state
        db_session.commit()
        dispatch_event('state_change', db_session, build)
        #TODO finish time

def download_logs(db_session, koji_session):
    def log_filter(filename):
        return filename.endswith('.log')

    to_download = db_session.query(Build)\
                   .filter(Build.logs_downloaded == False,
                           Build.state.in_(Build.FINISHED_STATES)).all()

    for build in to_download:
        out_dir = os.path.join(log_output_dir, str(build.id))
        for task in koji_session.getTaskChildren(build.task_id):
            if task['method'] == 'buildArch':
                arch_dir = os.path.join(out_dir, task['arch'])
                util.mkdir_if_absent(arch_dir)
                for file_name in koji_session.listTaskOutput(task['id']):
                    if file_name.endswith('.log'):
                        with open(os.path.join(arch_dir, file_name), 'w') as log_file:
                            log.info('Downloading {} for {}'.format(file_name, build.task_id))
                            log_file.write(koji_session.downloadTaskOutput(task['id'],
                                                                           file_name))
        build.logs_downloaded = True
        db_session.commit()
