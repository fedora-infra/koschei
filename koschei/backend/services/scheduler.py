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

from __future__ import print_function, absolute_import, division

from koschei import backend
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.service import Service
from koschei.models import Package, Build, Collection, KojiTask


class Scheduler(Service):
    koji_anonymous = False

    def __init__(self, session):
        super(Scheduler, self).__init__(session)

    def get_priorities(self):
        priority_expr = Package.current_priority_expression(
            collection=Collection,
            last_build=Build,
        )
        return self.db.query(Package.id, priority_expr)\
            .join(Package.collection)\
            .join(Package.last_build)\
            .filter(priority_expr != None)\
            .order_by(priority_expr.desc())\
            .all()

    def main(self):
        incomplete_builds_count = self.db.query(Build)\
            .filter(Build.state == Build.RUNNING)\
            .count()
        if incomplete_builds_count >= get_config('koji_config.max_builds'):
            self.log.debug("Not scheduling: {} incomplete builds"
                           .format(incomplete_builds_count))
            return

        for package_id, priority in self.get_priorities():
            if priority < get_config('priorities.build_threshold'):
                self.log.info("Not scheduling: no package above threshold")
                return
            package = self.db.query(Package).get(package_id)

            arches = self.db.query(KojiTask.arch)\
                .filter_by(build_id=package.last_build_id)\
                .all()
            arches = [arch for [arch] in arches]
            koji_load_threshold = get_config('koji_config.load_threshold')
            if koji_load_threshold < 1:
                koji_load = koji_util.get_koji_load(self.session.koji('primary'), arches)
                if koji_load > koji_load_threshold:
                    self.log.debug("Not scheduling {}: {} koji load"
                                   .format(package, koji_load))
                    return

            self.log.info('Scheduling build for {} in {}, priority {}'
                          .format(package.name, package.collection.name, priority))
            build = backend.submit_build(self.session, package)
            package.current_priority = None
            package.scheduler_skip_reason = None
            package.manual_priority = 0

            if not build:
                self.log.info("No SRPM found for {} in {}"
                              .format(package.name, package.collection.name))
                package.scheduler_skip_reason = Package.SKIPPED_NO_SRPM
                self.db.commit()
                continue

            self.db.commit()
            break
