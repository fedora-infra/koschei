# Copyright (C) 2014-2016  Red Hat, Inc.
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

from sqlalchemy import (func, union_all, extract, cast, Integer,
                        literal_column, text, Column)

from koschei import backend
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.service import Service
from koschei.models import Package, Build, Collection, KojiTask


def hours_since(what):
    return extract('EPOCH', literal_column('clock_timestamp()') - what) / 3600


class Scheduler(Service):
    koji_anonymous = False

    def __init__(self, session):
        super(Scheduler, self).__init__(session)
        self.calculation_timestamp = 0

    def get_dependency_priority_query(self):
        update_weight = get_config('priorities.package_update')
        cols = Column('pkg_id'), Column('priority')
        query = text("""
                SELECT package_id AS pkg_id, {w}/COALESCE(distance, 8) AS priority
                FROM unapplied_change
                WHERE prev_build_id IN (
                    SELECT DISTINCT ON(package.id) build.id AS build_id
                    FROM package JOIN build ON package.id = build.package_id
                    WHERE deps_resolved
                    ORDER BY package.id, build.id DESC
                )
                """.format(w=update_weight)).columns(*cols)
        return self.db.query(*cols).from_statement(query)

    def get_time_priority_query(self):
        t0 = get_config('priorities.t0')
        t1 = get_config('priorities.t1')
        a = get_config('priorities.build_threshold') / (math.log10(t1) - math.log10(t0))
        b = -a * math.log10(t0)
        log_arg = func.greatest(0.000001, hours_since(func.max(Build.started)))
        time_expr = func.greatest(a * func.log(log_arg) + b, -30)
        return self.db.query(Build.package_id.label('pkg_id'),
                             time_expr.label('priority'))\
                      .group_by(Build.package_id)

    def get_failed_build_priority_query(self):
        rank = func.rank().over(partition_by=Package.id,
                                order_by=Build.id.desc()).label('rank')
        sub = self.db.query(Package.id.label('pkg_id'), Build.state,
                            Build.deps_resolved, rank)\
                     .outerjoin(Build,
                                Package.id == Build.package_id)\
                     .subquery()
        failed_prio = get_config('priorities.failed_build_priority')
        return self.db.query(
            sub.c.pkg_id,
            literal_column(str(failed_prio)).label('priority')
        ).filter(
            ((sub.c.rank == 1) & ((sub.c.state == 5) | (sub.c.deps_resolved == False))) |
            ((sub.c.rank == 2) & (sub.c.state != 5))
        ).group_by(sub.c.pkg_id).having(func.count(sub.c.pkg_id) == 2)

    def get_priority_queries(self):
        return [
            self.get_dependency_priority_query(),
            self.get_time_priority_query(),
            self.get_failed_build_priority_query(),
        ]

    def get_incomplete_builds_query(self):
        return self.db.query(Build.package_id).filter(Build.state == Build.RUNNING)

    def get_priorities(self):
        incomplete_builds = self.get_incomplete_builds_query()
        union_query = union_all(*self.get_priority_queries()).alias('un')
        pkg_id = union_query.c.pkg_id
        current_priority = cast(func.sum(union_query.c.priority),
                                Integer).label('curr_priority')
        priorities = self.db.query(pkg_id, current_priority)\
                            .group_by(pkg_id).subquery()
        computed_priority = func.coalesce(priorities.c.curr_priority *
                                          Collection.priority_coefficient, 0)
        priority_expr = (computed_priority + Package.manual_priority +
                         Package.static_priority)
        return self.db.query(Package.id, priority_expr, Package.current_priority)\
                      .join(Package.collection)\
                      .outerjoin(priorities, Package.id == priorities.c.pkg_id)\
                      .filter((Package.resolved == True) |
                              (Package.resolved == None))\
                      .filter(Package.id.notin_(incomplete_builds.subquery()))\
                      .filter(Package.blocked == False)\
                      .filter(Package.tracked == True)\
                      .order_by(priority_expr.desc())\
                      .all()

    def persist_priorities(self, prioritized):
        if not prioritized:
            return
        threshold = get_config('priorities.priority_update_threshold', 100)
        to_update = [{'package_id': package_id, 'priority': priority}
                     for (package_id, priority, prev_prio) in prioritized
                     if abs((priority or 0) - (prev_prio or 0)) > threshold]
        if to_update:
            self.lock_package_table()
            self.db.execute(
                text("""
                    UPDATE package
                    SET current_priority = :priority
                    WHERE id = :package_id
                """),
                to_update,
            )
        self.db.commit()
        self.calculation_timestamp = time.time()

    def lock_package_table(self):
        self.db.execute("LOCK TABLE package IN EXCLUSIVE MODE;")

    def main(self):
        prioritized = self.get_priorities()
        self.db.rollback()  # no-op, ends the transaction
        if (time.time() - self.calculation_timestamp >
                get_config('priorities.calculation_interval')):
            self.persist_priorities(prioritized)
        incomplete_builds = self.get_incomplete_builds_query().count()
        if incomplete_builds >= get_config('koji_config.max_builds'):
            self.log.debug("Not scheduling: {} incomplete builds"
                           .format(incomplete_builds))
            return

        for package_id, priority, _ in prioritized:
            if priority < get_config('priorities.build_threshold'):
                self.log.info("Not scheduling: no package above threshold")
                return
            package = self.db.query(Package).get(package_id)
            if not package.collection.latest_repo_resolved:
                self.log.info("Skipping {}: {} buildroot not resolved"
                              .format(package, package.collection))
                continue

            # a package was chosen
            arches = self.db.query(KojiTask.arch)\
                .filter_by(build_id=package.last_build_id)\
                .all()
            arches = [arch for [arch] in arches]
            koji_load = koji_util.get_koji_load(self.session.koji('primary'), arches)
            if koji_load > get_config('koji_config.load_threshold'):
                self.log.debug("Not scheduling {}: {} koji load"
                               .format(package, koji_load))
                return

            self.log.info('Scheduling build for {}, priority {}'
                          .format(package.name, priority))
            build = backend.submit_build(self.session, package)
            package.current_priority = None
            package.scheduler_skip_reason = None
            package.manual_priority = 0

            if not build:
                self.log.debug("No SRPM found for {}".format(package.name))
                package.scheduler_skip_reason = Package.SKIPPED_NO_SRPM
                self.db.commit()
                continue

            self.db.commit()
            break
