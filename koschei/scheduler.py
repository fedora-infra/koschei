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
from .models import Package, Build, DependencyChange
from .service import service_main
from . import util
from sqlalchemy import func, union_all
from datetime import datetime

import json
import math
import logging

priority_threshold = util.config['priorities']['build_threshold']
max_builds = util.config['koji_config']['max_builds']
load_threshold = util.config['koji_config'].get('load_threshold')

log = logging.getLogger('scheduler')

def get_priority_queries(db_session):
    prio = ('manual', Package.manual_priority), ('static', Package.static_priority)
    priorities = {name: db_session.query(Package.id.label('pkg_id'),
                                         col.label('priority'))
                    for name, col in prio}
    priorities['dependency'] = DependencyChange.get_priority_query(db_session)
    t0 = util.config['priorities']['t0']
    t1 = util.config['priorities']['t1']
    a = priority_threshold / (math.log10(t1) - math.log10(t0))
    b = -a * math.log10(t0)
    time_expr = func.greatest(a * func.log(Build.time_since_last_build_expr()
                                           + 0.00001) + b, -30)
    priorities['time'] = db_session.query(Build.package_id.label('pkg_id'),
                                          time_expr.label('priority'))\
                                   .group_by(Build.package_id)
    return priorities

@service_main(koji_anonymous=False)
def schedule_builds(db_session, koji_session):
    incomplete_builds = db_session.query(func.count(Build.id))\
                                  .filter(Build.state == Build.RUNNING)\
                                  .scalar()
    limit = max_builds - incomplete_builds
    if limit <= 0:
        return
    queries = get_priority_queries(db_session).values()
    union_query = union_all(*queries).alias('un')
    pkg_id = union_query.c.pkg_id
    current_priority = func.sum(union_query.c.priority).label('curr_priority')
    candidates = db_session.query(pkg_id, current_priority)\
                           .having(current_priority >= priority_threshold)\
                           .group_by(pkg_id).subquery()
    unfinished = db_session.query(Build.package_id)\
                           .filter(Build.state.in_(Build.UNFINISHED_STATES))\
                           .subquery()
    to_schedule = db_session.query(Package, candidates.c.curr_priority)\
                            .join(candidates, Package.id == candidates.c.pkg_id)\
                            .filter(Package.state == Package.OK)\
                            .filter(Package.id.notin_(unfinished))\
                            .order_by(candidates.c.curr_priority.desc())\
                            .first()

    if to_schedule:
        if load_threshold and util.get_koji_load(koji_session) > load_threshold:
            return
        package, priority = to_schedule
        log.info('Scheduling build for {}, priority {}'\
                 .format(package.name, priority))
        submit_build(db_session, koji_session, package)
        db_session.commit()

def submit_build(db_session, koji_session, package):
    build = Build(package_id=package.id, state=Build.RUNNING)
    name = package.name
    build.state = Build.RUNNING
    build_opts = None
    if package.build_opts:
        build_opts = json.loads(package.build_opts)
    srpm, srpm_url = util.get_last_srpm(koji_session, name) or (None, None)
    if srpm_url:
        package.manual_priority = 0
        build.task_id = util.koji_scratch_build(koji_session, name,
                                                srpm_url, build_opts)
        build.started = datetime.now()
        build.epoch = srpm['epoch']
        build.version = srpm['version']
        build.release = srpm['release']
        db_session.add(build)
        db_session.flush()
        DependencyChange.build_submitted(db_session, build)
    else:
        package.state = Package.RETIRED
