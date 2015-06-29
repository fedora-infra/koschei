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
from sqlalchemy import (func, union_all, extract, cast, Integer, case, null,
                        literal_column)
from sqlalchemy.sql.functions import coalesce

from . import util
from .models import Package, Build, DependencyChange, is_buildroot_broken
from .service import KojiService
from .backend import Backend


def hours_since(what):
    return extract('EPOCH', datetime.now() - what) / 3600


class Scheduler(KojiService):
    koji_anonymous = False

    priority_conf = util.config['priorities']
    priority_threshold = priority_conf['build_threshold']
    failed_priority = priority_conf['failed_build_priority']
    max_builds = util.config['koji_config']['max_builds']
    load_threshold = util.config['koji_config']['load_threshold']

    def __init__(self, backend=None, *args, **kwargs):
        super(Scheduler, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log,
                                          db=self.db,
                                          koji_session=self.koji_session)

    def get_dependency_priority_query(self):
        update_weight = self.priority_conf['package_update']
        distance = coalesce(DependencyChange.distance, 8)
        return self.db.query(DependencyChange.package_id.label('pkg_id'),
                             (update_weight / distance)
                             .label('priority'))\
                      .filter_by(applied_in_id=None)

    def get_time_priority_query(self):
        t0 = self.priority_conf['t0']
        t1 = self.priority_conf['t1']
        a = self.priority_threshold / (math.log10(t1) - math.log10(t0))
        b = -a * math.log10(t0)
        time_expr = func.greatest(a * func.log(
            hours_since(func.max(Build.started)) + 0.00001) + b, -30)
        return self.db.query(Build.package_id.label('pkg_id'),
                             time_expr.label('priority'))\
                      .group_by(Build.package_id)

    def get_failed_build_priority_query(self):
        rank = func.rank().over(partition_by=Package.id,
                                order_by=Build.id.desc()).label('rank')
        sub = self.db.query(Package.id.label('pkg_id'), Build.state, rank)\
                     .outerjoin(Build,
                                Package.id == Build.package_id)\
                     .subquery()
        return self.db.query(sub.c.pkg_id,
                             literal_column(str(self.failed_priority))
                             .label('priority'))\
                      .filter(((sub.c.rank == 1) & (sub.c.state == 5)) |
                              ((sub.c.rank == 2) & (sub.c.state != 5)))\
                      .group_by(sub.c.pkg_id)\
                      .having(func.count(sub.c.pkg_id) == 2)

    def get_priority_queries(self):
        prio = (('manual', Package.manual_priority),
                ('static', Package.static_priority))
        priorities = {name: self.db.query(Package.id.label('pkg_id'),
                                          col.label('priority'))
                      for name, col in prio}
        priorities['dependency'] = self.get_dependency_priority_query()
        priorities['time'] = self.get_time_priority_query()
        priorities['failed_build'] = self.get_failed_build_priority_query()
        return priorities

    def get_scheduled_package(self):
        if is_buildroot_broken(self.db):
            return
        incomplete_builds = self.db.query(Build.package_id)\
                                .filter(Build.state == Build.RUNNING)
        queries = self.get_priority_queries().values()
        union_query = union_all(*queries).alias('un')
        pkg_id = union_query.c.pkg_id
        current_priority = cast(func.sum(union_query.c.priority),
                                Integer).label('curr_priority')
        priorities = self.db.query(pkg_id, current_priority)\
                            .group_by(pkg_id).subquery()
        prioritized = self.db.query(Package.id, priorities.c.curr_priority)\
                             .join(priorities,
                                   Package.id == priorities.c.pkg_id)\
                             .filter((Package.resolved == True) |
                                     (Package.resolved == None))\
                             .filter(Package.id.notin_(
                                 incomplete_builds.subquery()))\
                             .filter(Package.ignored == False)\
                             .order_by(priorities.c.curr_priority.desc())\
                             .all()

        if not prioritized or incomplete_builds.count() >= self.max_builds:
            return None

        self.db.rollback()
        self.lock_package_table()
        # pylint: disable=E1101
        self.db.execute(Package.__table__.update()
                        .values(current_priority=case(prioritized,
                                                      value=Package.id,
                                                      else_=null())))
        self.db.commit()
        package = self.db.query(Package).get(prioritized[0][0])
        if (package.current_priority >= self.priority_threshold
                and util.get_koji_load(self.koji_session)
                < self.load_threshold):
            return package

    def lock_package_table(self):
        self.db.execute("LOCK TABLE package IN EXCLUSIVE MODE;")

    def main(self):
        package = self.get_scheduled_package()
        if package:
            newer_build = self.backend.get_newer_build_if_exists(package)
            if newer_build:
                self.db.rollback()
                self.backend.register_real_build(package, newer_build)
                self.db.commit()
                self.main()
            else:
                self.log.info('Scheduling build for {}, priority {}'
                              .format(package.name, package.current_priority))
                self.backend.submit_build(package)
                self.db.commit()
