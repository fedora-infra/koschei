from __future__ import print_function
from models import Session, Package, Build, PriorityChange
from sqlalchemy import func

import logging

priority_threshold = 30
time_slice = 4

log = logging.getLogger('scheduler')

def schedule_builds(db_session):
    candidates = db_session.query(Package, func.sum(PriorityChange.value))\
                                  .filter(Package.watched == True,
                                          PriorityChange.applied_in_id == None)\
                                  .outerjoin(PriorityChange).group_by(Package)
    for pkg, priority in candidates:
        # TODO do this in query
        if priority < priority_threshold:
            continue
        if db_session.query(Build).filter_by(package_id=pkg.id)\
                               .filter(Build.state.in_(Build.UNFINISHED_STATES))\
                               .count() == 0:
            build = Build(package_id=pkg.id, state=Build.SCHEDULED)
            db_session.add(build)
            db_session.commit()
            log.info('Scheduling build {} for {}'.format(build.id, pkg.name))
