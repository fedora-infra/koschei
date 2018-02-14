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

from koschei import backend
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.service import Service
from koschei.models import Package, Build, Collection


class Scheduler(Service):
    koji_anonymous = False

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

    def skip_no_srpm(self, package):
        self.log.info("No SRPM found for {} in {}"
                      .format(package.name, package.collection.name))
        package.scheduler_skip_reason = Package.SKIPPED_NO_SRPM
        package.resolved = None
        package.last_build_id = None
        package.last_complete_build_id = None
        package.last_complete_build_state = None
        self.db.query(Build)\
            .filter_by(package_id=package.id)\
            .filter_by(last_complete=True)\
            .update({'last_complete': False})
        self.db.commit()

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

            koji_session = self.session.koji('primary')
            all_arches = koji_util.get_koji_arches_cached(
                self.session,
                koji_session,
                package.collection.build_tag,
            )
            arches = koji_util.get_srpm_arches(
                koji_session=self.session.koji('secondary'),
                all_arches=all_arches,
                nvra=package.srpm_nvra,
                arch_override=package.arch_override,
            )
            if arches is None:
                self.skip_no_srpm(package)
                continue
            if not arches:
                self.log.info("Skipping {}: no allowed arch".format(package.name))
                package.scheduler_skip_reason = Package.SKIPPED_NO_ARCH
                # FIXME we don't have a better way how to get package out of
                # scheduler's way
                package.manual_priority -= 1000
                continue
            koji_load_threshold = get_config('koji_config.load_threshold')
            if koji_load_threshold < 1:
                koji_load = koji_util.get_koji_load(
                    koji_session=koji_session,
                    all_arches=all_arches,
                    arches=arches,
                )
                if koji_load > koji_load_threshold:
                    self.log.debug("Not scheduling {}: {} koji load"
                                   .format(package, koji_load))
                    return

            self.log.info('Scheduling build for {} in {}, priority {}'
                          .format(package.name, package.collection.name, priority))
            build = backend.submit_build(
                self.session,
                package,
                arch_override=None if 'noarch' in arches else arches,
            )
            package.current_priority = None
            package.scheduler_skip_reason = None
            package.manual_priority = 0

            if not build:
                self.skip_no_srpm(package)
                continue

            self.db.commit()
            break
