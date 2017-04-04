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

from __future__ import print_function, absolute_import

import koji

from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from koschei import plugin, backend
from koschei.models import Build, ResourceConsumptionStats, ScalarStats
from koschei.backend.service import Service
from koschei.backend.koji_util import itercall


class Polling(Service):
    def __init__(self, session):
        super(Polling, self).__init__(session)

    def poll_builds(self):
        self.log.info('Polling running Koji tasks...')
        running_builds = self.db.query(Build)\
                                .filter_by(state=Build.RUNNING)

        infos = itercall(self.session.koji('primary'), running_builds,
                         lambda k, b: k.getTaskInfo(b.task_id))

        for task_info, build in zip(infos, running_builds):
            try:
                name = build.package.name
                self.log.info('Polling task {id} ({name}): task_info={info}'
                              .format(id=build.task_id, name=name,
                                      info=task_info))
                state = koji.TASK_STATES[task_info['state']]
                backend.update_build_state(self.session, build, state)
            except (StaleDataError, ObjectDeletedError):
                # build was deleted concurrently
                self.db.rollback()
                continue

    def main(self):
        self.poll_builds()
        self.log.info('Polling Koji packages...')
        backend.refresh_packages(self.session)
        self.db.commit()
        self.db.close()
        plugin.dispatch_event('polling_event', self.session)
        self.db.commit()
        self.db.close()
        self.log.info('Polling latest real builds...')
        backend.refresh_latest_builds(self.session)
        self.db.commit()
        self.log.info('Refreshing statistics...')
        self.db.refresh_mv(ResourceConsumptionStats, ScalarStats)
        self.db.commit()
        self.log.info('Polling finished')
