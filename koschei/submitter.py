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

from datetime import datetime
from sqlalchemy.sql.expression import func

from . import util
from .models import Build, Session, Package, PackageStateChange, DependencyChange

log = logging.getLogger('submitter')

max_builds = util.config['koji_config']['max_builds']

def submit_builds(db_session, koji_session):
    load_threshold = util.config['koji_config'].get('load_threshold')
    if load_threshold and get_koji_load(koji_session) > load_threshold:
        return
    running_builds_count = db_session.query(func.count(Build.id))\
                                     .filter_by(state=Build.RUNNING).scalar()
    scheduled_builds = db_session.query(Build).filter_by(state=Build.SCHEDULED)\
                                 .order_by(Build.id)\
                                 .limit(max_builds - running_builds_count)
    for build in scheduled_builds:
        name = build.package.name
        build.state = Build.RUNNING
        try:
            build.task_id = util.koji_scratch_build(koji_session, name)
        except util.PackageBlocked:
            package = build.package
            package.state = Package.RETIRED
            db_session.delete(build)
            db_session.commit()
            continue
        build.started = datetime.now()
        build.package.manual_priority = 0
        for cls in PackageStateChange, DependencyChange:
            cls.build_submitted(db_session, build)
        db_session.commit()

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
        if state == Build.CANCELED:
            db_session.delete(build)
            log.info('Deleting build {build} because it was canceled'\
                     .format(build))
        else:
            log.info('Setting build {build} state to {state}'\
                      .format(build=build, state=Build.REV_STATE_MAP[state]))
            build.state = state
        db_session.commit()
        #TODO finish time

def get_koji_load(koji_session):
    hosts = koji_session.listHosts(ready=True)
    capacity = sum(host['capacity'] for host in hosts)
    load = sum(host['task_load'] for host in hosts)
    return load / capacity

def main():
    import time
    db_session = Session()
    koji_session = util.create_koji_session()
    print("submitter started")
    poll = 0
    while True:
        submit_builds(db_session, koji_session)
        poll += 1
        if poll > 0:
            poll_tasks(db_session, koji_session)
            poll = -1200
        db_session.expire_all()
        time.sleep(3)

if __name__ == '__main__':
    main()
