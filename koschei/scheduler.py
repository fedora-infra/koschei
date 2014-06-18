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
from models import Session, Package, Build
from sqlalchemy import func, union_all

import logging

from .plugins import dispatch_event

priority_threshold = 30

log = logging.getLogger('scheduler')

def schedule_builds(db_session):
    priority_queries = dispatch_event('get_priority_query', db_session)
    manual_priority = db_session.query(Package.id, Package.manual_priority)\
                                .filter(Package.watched == True).subquery()
    static_priority = db_session.query(Package.id, Package.static_priority)\
                                .filter(Package.watched == True).subquery()
    union_query = union_all(*[q.select() for q in [static_priority, manual_priority]
                                                   + priority_queries])
    priorities = db_session.query(Package.id)\
                           .select_entity_from(union_query)\
                           .having(func.sum(Package.static_priority)
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
    # Bump priority of not built packages
    db_session.query(Package).outerjoin(Build)\
            .filter(Build.id == None)\
            .update({Package.manual_priority: Package.manual_priority + 1})

def main():
    import time
    db_session = Session()
    print("scheduler started")
    while True:
        schedule_builds(db_session)
        time.sleep(3)

if __name__ == '__main__':
    main()
