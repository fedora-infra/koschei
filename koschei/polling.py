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

from .models import Build
from .service import KojiService
from .backend import update_build_state

class Polling(KojiService):
    def main(self):
        running_builds = self.db_session.query(Build).filter_by(state=Build.RUNNING)
        for build in running_builds:
            name = build.package.name
            if not build.task_id:
                self.log.warn('No task id assigned to build {0})'.format(build))
            else:
                task_info = self.koji_session.getTaskInfo(build.task_id)
                self.log.debug('Polling task {id} ({name}): task_info={info}'\
                               .format(id=build.task_id, name=name, info=task_info))
                state = koji.TASK_STATES.getvalue(task_info['state'])
                update_build_state(self.db_session, build, state)
