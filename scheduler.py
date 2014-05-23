from __future__ import print_function
from models import Session, Package, Build

import logging

priority_threshold = 100
time_slice = 4

log = logging.getLogger('scheduler')

def schedule_builds():
    session = Session()
    candidates = session.query(Package)\
            .filter(Package.priority >= priority_threshold)
    for pkg in candidates:
        if session.query(Build).filter_by(package_id=pkg.id)\
                               .filter(Build.state.in_(Build.UNFINISHED_STATES))\
                               .count() == 0:
            build = Build(package_id=pkg.id, state='scheduled')
            session.add(build)
            session.commit()
            log.info('Scheduling build {} for {}'.format(build.id, pkg.name))

if __name__ == '__main__':
    import time
    while True:
        schedule_builds()
        time.sleep(time_slice)

