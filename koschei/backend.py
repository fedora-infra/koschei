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

import logging
import json

from datetime import datetime

from . import util
from .models import Package, Build, DependencyChange

log = logging.getLogger('backend')

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
        build_registered(db_session, build)
    else:
        package.state = Package.RETIRED

def update_build_state(db_session, build, state):
    if state in Build.KOJI_STATE_MAP:
        state = Build.KOJI_STATE_MAP[state]
        if state == Build.CANCELED:
            log.info('Deleting build {0} because it was canceled'\
                     .format(build))
            db_session.delete(build)
        else:
            log.info('Setting build {build} state to {state}'\
                      .format(build=build, state=Build.REV_STATE_MAP[state]))
            build.state = state
        db_session.commit()
        #TODO finish time

def build_registered(db_session, build):
    db_session.query(DependencyChange).filter_by(package_id=build.package_id)\
                                      .filter_by(applied_in_id=None)\
                                      .update({'applied_in_id': build.id})
