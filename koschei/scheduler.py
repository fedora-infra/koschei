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
from .models import Session, Package, Build, DependencyChange, PackageStateChange
from sqlalchemy import func, union_all

import logging

from .plugin import dispatch_event

priority_threshold = 30

log = logging.getLogger('scheduler')

def get_priority_queries(db_session):
    prio = ('manual', Package.manual_priority), ('static', Package.static_priority)
    priorities = {name: db_session.query(Package.id, col) for name, col in prio}
    changes = ('dependency', DependencyChange), ('state', PackageStateChange)
    priorities.update({name: cls.get_priority_query(db_session) for name, cls in changes})
    #FIXME move time here
    priorities['time'] = dispatch_event('get_priority_query', db_session)[0]

def schedule_builds(db_session):
    queries = get_priority_queries(db_session).values()
    union_query = union_all(*(q.subquery().select() for q in queries))
    priorities = db_session.query(Package.id)\
                           .select_entity_from(union_query)\
                           .having(func.sum(Package.manual_priority)
                                   >= priority_threshold)\
                           .group_by(Package.id)
    for pkg_id in [p.id for p in priorities]:
        if db_session.query(Build).filter_by(package_id=pkg_id)\
                               .filter(Build.state.in_(Build.UNFINISHED_STATES))\
                               .count() == 0:
            build = Build(package_id=pkg_id, state=Build.SCHEDULED)
            db_session.add(build)
            db_session.commit()
            log.info('Scheduling build {} for {}'.format(build.id, build.package.name))

def main():
    import time
    db_session = Session()
    print("scheduler started")
    while True:
        schedule_builds(db_session)
        time.sleep(3)

if __name__ == '__main__':
    main()
