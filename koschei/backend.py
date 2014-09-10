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

import json

from datetime import datetime
from contextlib import contextmanager

from . import util
from .models import Build, DependencyChange, KojiTask, Session
from .plugin import Event

class PackageStateUpdateEvent(Event):
    def __init__(self, package, prev_state, new_state):
        self.package = package
        self.prev_state = prev_state
        self.new_state = new_state

@contextmanager
def watch_package_state(package):
    prev_pkg_state = package.state_string
    yield
    Session.object_session(package).flush()
    new_pkg_state = package.state_string
    if prev_pkg_state != new_pkg_state:
        PackageStateUpdateEvent(package, prev_pkg_state, new_pkg_state).dispatch()

class Backend(object):
    def __init__(self, log, db_session, koji_session):
        self.log = log
        self.db_session = db_session
        self.koji_session = koji_session

    def submit_build(self, package):
        build = Build(package_id=package.id, state=Build.RUNNING)
        name = package.name
        build.state = Build.RUNNING
        build_opts = None
        if package.build_opts:
            build_opts = json.loads(package.build_opts)
        srpm, srpm_url = util.get_last_srpm(self.koji_session, name) or (None, None)
        if srpm_url:
            package.manual_priority = 0
            build.task_id = util.koji_scratch_build(self.koji_session, name,
                                                    srpm_url, build_opts)
            build.started = datetime.now()
            build.epoch = srpm['epoch']
            build.version = srpm['version']
            build.release = srpm['release']
            self.db_session.add(build)
            self.db_session.flush()
            self.db_session.query(DependencyChange).filter_by(package_id=build.package_id)\
                                                   .filter_by(applied_in_id=None)\
                                                   .update({'applied_in_id': build.id})
        else:
            package.ignored = True

    def update_build_state(self, build, state):
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            if state == Build.CANCELED:
                self.log.info('Deleting build {0} because it was canceled'\
                              .format(build))
                self.db_session.delete(build)
            else:
                self.log.info('Setting build {build} state to {state}'\
                              .format(build=build, state=Build.REV_STATE_MAP[state]))
                with watch_package_state(build.package):
                    build.state = state
            if state in (Build.COMPLETE, Build.FAILED):
                self.build_completed(build)
            self.db_session.commit()

    def build_completed(self, build):
        task_info = self.koji_session.getTaskInfo(build.task_id)
        if task_info['completion_time']:
            build.finished = util.parse_koji_time(task_info['completion_time'])
        else:
            # When fedmsg delivery is fast, the time is not set yet
            build.finished = datetime.now()
        subtasks = self.koji_session.getTaskChildren(build.task_id, request=True)
        build_arch_tasks = [task for task in subtasks if task['method'] == 'buildArch']
        with util.skip_on_integrity_violation(self.db_session):
            for task in build_arch_tasks:
                try:
                    # They all have the same repo_id, right?
                    build.repo_id = task['request'][4]['repo_id']
                except KeyError:
                    pass
                db_task = KojiTask(build_id=build.id, task_id=task['id'],
                                   state=task['state'], started=task['create_time'],
                                   finished=task['completion_time'], arch=task['arch'])
                self.db_session.add(db_task)
