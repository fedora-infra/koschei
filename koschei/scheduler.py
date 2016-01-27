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
import time

from sqlalchemy import (func, union_all, extract, cast, Integer, case, null,
                        literal_column)
from sqlalchemy.sql.functions import coalesce

from . import util
from .models import Package, Build, UnappliedChange, is_buildroot_broken
from .service import KojiService
from .backend import Backend


def hours_since(what):
    return extract('EPOCH', literal_column('clock_timestamp()') - what) / 3600


class Scheduler(KojiService):
    koji_anonymous = False

    priority_conf = util.config['priorities']
    priority_threshold = priority_conf['build_threshold']
    failed_priority = priority_conf['failed_build_priority']
    max_builds = util.primary_koji_config['max_builds']
    load_threshold = util.primary_koji_config['load_threshold']
    calculation_interval = util.config['priorities']['calculation_interval']

    def __init__(self, backend=None, *args, **kwargs):
        super(Scheduler, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log,
                                          db=self.db,
                                          koji_session=self.koji_session,
                                          secondary_koji=self.secondary_koji)
        self.calculation_timestamp = 0

    def get_dependency_priority_query(self):
        update_weight = self.priority_conf['package_update']
        # pylint: disable=E1120
        distance = coalesce(UnappliedChange.distance, 8)
        # inner join with package last build to get rid of outdated dependency changes
        return self.db.query(UnappliedChange.package_id.label('pkg_id'),
                             (update_weight / distance)
                             .label('priority'))\
                      .join(Package,
                            Package.last_build_id == UnappliedChange.prev_build_id)

    def get_time_priority_query(self):
        t0 = self.priority_conf['t0']
        t1 = self.priority_conf['t1']
        a = self.priority_threshold / (math.log10(t1) - math.log10(t0))
        b = -a * math.log10(t0)
        log_arg = func.greatest(0.000001, hours_since(func.max(Build.started)))
        time_expr = func.greatest(a * func.log(log_arg) + b, -30)
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

    def get_incomplete_builds_query(self):
        return self.db.query(Build.package_id).filter(Build.state == Build.RUNNING)

    def get_priorities(self):
        incomplete_builds = self.get_incomplete_builds_query()
        queries = self.get_priority_queries().values()
        union_query = union_all(*queries).alias('un')
        pkg_id = union_query.c.pkg_id
        current_priority = cast(func.sum(union_query.c.priority),
                                Integer).label('curr_priority')
        priorities = self.db.query(pkg_id, current_priority)\
                            .group_by(pkg_id).subquery()
        return self.db.query(Package.id, priorities.c.curr_priority)\
                      .join(priorities, Package.id == priorities.c.pkg_id)\
                      .filter((Package.resolved == True) |
                              (Package.resolved == None))\
                      .filter(Package.id.notin_(incomplete_builds.subquery()))\
                      .filter(Package.blocked == False)\
                      .filter(Package.tracked == True)\
                      .order_by(priorities.c.curr_priority.desc())\
                      .all()

    def persist_priorities(self, prioritized):
        if not prioritized:
            return
        self.lock_package_table()
        # pylint: disable=E1101
        self.db.execute(Package.__table__.update()
                        .values(current_priority=case(prioritized,
                                                      value=Package.id,
                                                      else_=null())))
        self.db.commit()
        self.calculation_timestamp = time.time()

    def lock_package_table(self):
        self.db.execute("LOCK TABLE package IN EXCLUSIVE MODE;")

    def main(self):
        if is_buildroot_broken(self.db):
            self.log.debug("Not scheduling: buildroot broken")
            return
        prioritized = self.get_priorities()
        self.db.rollback()  # no-op, ends the transaction
        if time.time() - self.calculation_timestamp > self.calculation_interval:
            self.persist_priorities(prioritized)
        incomplete_builds = self.get_incomplete_builds_query().count()
        if incomplete_builds >= self.max_builds:
            self.log.debug("Not scheduling: {} incomplete builds"
                           .format(incomplete_builds))
            return
        koji_load = util.get_koji_load(self.koji_session)
        if koji_load > self.load_threshold:
            self.log.debug("Not scheduling: {} koji load"
                           .format(koji_load))
            return

        repo_id = util.get_latest_repo(self.koji_session).get('id')

        for package_id, priority in prioritized:
            if priority < self.priority_threshold:
                self.log.debug("Not scheduling: no package above threshold")
                return
            package = self.db.query(Package).get(package_id)
            newer_build = self.backend.get_newer_build_if_exists(package)
            if newer_build:
                self.backend.register_real_build(package, newer_build)
                self.db.commit()
                self.log.debug("Skipping {} due to real build"
                               .format(package))
                continue
            if (repo_id and package.last_complete_build and
                    package.last_complete_build.repo_id >= repo_id):
                self.log.debug("Skipping {} due to repo_id"
                               .format(package))
                continue

            # a package was chosen
            self.log.info('Scheduling build for {}, priority {}'
                          .format(package.name, priority))
            build = self.backend.submit_build(package)
            if not build:
                self.log.debug("No SRPM found for {}".format(package.name))
                continue

            self.db.commit()
            break
