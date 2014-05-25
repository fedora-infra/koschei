from __future__ import print_function

import logging
import koji

from util import koji_scratch_build
from models import Package, Build

log = logging.getLogger('submitter')

def submit_builds(db_session, koji_session):
    scheduled_builds = db_session.query(Build).filter_by(state=Build.SCHEDULED)
    for build in scheduled_builds:
        name = build.package.name
        build.state = 'running'
        build.task_id = koji_scratch_build(koji_session, name)
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
            state_transitions = {
                    'CLOSED': Build.COMPLETE,
                    'CANCELED': Build.CANCELED,
                    'FAILED': Build.FAILED,
                }
            if state in state_transitions.keys():
                state = state_transitions[state]
                log.info('Setting build {build} state to {state}'\
                          .format(build=build, state=Build.REV_STATE_MAP[state]))
                build.state = state
                build.package.priority = 0
                db_session.commit()
