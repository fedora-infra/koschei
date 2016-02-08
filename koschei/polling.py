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

import koji

from . import util, plugin
from .models import Build
from .service import KojiService
from .backend import Backend


class Polling(KojiService):
    def __init__(self, backend=None, *args, **kwargs):
        super(Polling, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log, db=self.db,
                                          koji_sessions=self.koji_sessions)

    def poll_builds(self):
        self.log.debug('Polling running Koji tasks...')
        running_builds = self.db.query(Build)\
                                .filter_by(state=Build.RUNNING)

        infos = util.itercall(self.koji_sessions['primary'], running_builds,
                              lambda k, b: k.getTaskInfo(b.task_id))

        for task_info, build in zip(infos, running_builds):
            name = build.package.name
            self.log.debug('Polling task {id} ({name}): task_info={info}'
                           .format(id=build.task_id, name=name,
                                   info=task_info))
            state = koji.TASK_STATES.getvalue(task_info['state'])
            self.backend.update_build_state(build, state)

    def poll_repo(self):
        self.log.debug('Polling latest Koji repo')
        self.backend.poll_repo()

    def main(self):
        self.poll_builds()
        self.poll_repo()
        self.log.debug('Polling Koji packages...')
        self.backend.refresh_packages()
        self.log.debug('Polling latest real builds...')
        self.backend.refresh_latest_builds()
        plugin.dispatch_event('polling_event', self.backend)
        self.db.commit()
        self.log.debug('Polling finished')
