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

import math

from datetime import datetime
from sqlalchemy import func, union_all, extract
from sqlalchemy.sql import literal_column, true
from sqlalchemy.sql.functions import coalesce

from . import util
from .models import Package, Build, DependencyChange
from .service import KojiService
from .backend import Backend

def hours_since(what):
    return extract('EPOCH', datetime.now() - what) / 3600

class Scheduler(KojiService):
    koji_anonymous = False

    priority_conf = util.config['priorities']
    priority_threshold = priority_conf['build_threshold']
    max_builds = util.config['koji_config']['max_builds']
    load_threshold = util.config['koji_config']['load_threshold']

    def __init__(self, backend=None, *args, **kwargs):
        super(Scheduler, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log, db_session=self.db_session,
                                          koji_session=self.koji_session)

    def get_dependency_priority_query(self):
        update_weight = self.priority_conf['package_update']
        distance = coalesce(DependencyChange.distance, 8)
        return self.db_session.query(DependencyChange.package_id.label('pkg_id'),
                                     (update_weight / distance)\
                                            .label('priority'))\
                              .filter_by(applied_in_id=None)

    def get_time_priority_query(self):
        t0 = self.priority_conf['t0']
        t1 = self.priority_conf['t1']
        a = self.priority_threshold / (math.log10(t1) - math.log10(t0))
        b = -a * math.log10(t0)
        time_expr = func.greatest(a * func.log(hours_since(func.max(Build.started))
                                               + 0.00001) + b, -30)
        return self.db_session.query(Build.package_id.label('pkg_id'),
                                     time_expr.label('priority'))\
                              .group_by(Build.package_id)

    def get_priority_queries(self):
        prio = ('manual', Package.manual_priority), ('static', Package.static_priority)
        priorities = {name: self.db_session.query(Package.id.label('pkg_id'),
                                                  col.label('priority'))
                        for name, col in prio}
        priorities['dependency'] = self.get_dependency_priority_query()
        priorities['time'] = self.get_time_priority_query()
        return priorities

    def main(self):
        incomplete_builds = self.db_session.query(Build.package_id)\
                                           .filter(Build.state == Build.RUNNING)
        if incomplete_builds.count() >= self.max_builds:
            return
        queries = self.get_priority_queries().values()
        union_query = union_all(*queries).alias('un')
        pkg_id = union_query.c.pkg_id
        current_priority = func.sum(union_query.c.priority).label('curr_priority')
        candidates = self.db_session.query(pkg_id, current_priority)\
                                    .having(current_priority >= self.priority_threshold)\
                                    .group_by(pkg_id).subquery()
        to_schedule = self.db_session.query(Package, candidates.c.curr_priority)\
                                     .join(candidates, Package.id == candidates.c.pkg_id)\
                                     .join(Package.resolution_result)\
                                     .filter(literal_column('last_resolution.resolved')
                                             == true())\
                                     .filter(Package.id.notin_(
                                                incomplete_builds.subquery()))\
                                     .filter(Package.ignored == False)\
                                     .order_by(candidates.c.curr_priority.desc())\
                                     .first()

        if to_schedule:
            if util.get_koji_load(self.koji_session) > self.load_threshold:
                return
            package, priority = to_schedule
            self.log.info('Scheduling build for {}, priority {}'\
                          .format(package.name, priority))
            self.backend.submit_build(package)
            self.db_session.commit()
