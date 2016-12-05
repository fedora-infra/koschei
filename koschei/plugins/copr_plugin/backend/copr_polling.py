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

from koschei.config import get_config
from koschei.plugin import listen_event
from koschei.models import CoprRebuild, Build

# pylint:disable=import-error
from copr_plugin import copr_client


def refresh_build_state(session, build):
    copr_build = copr_client.get_build_details(build.copr_build_id).data
    state = copr_build['chroots'][get_config('copr.chroot_name')]
    state_map = {
        'succeeded': Build.COMPLETE,
        'failed': Build.FAILED,
        'canceled': Build.CANCELED,
        'skipped': Build.CANCELED,
    }
    if state in state_map:
        build.state = state_map[state]
        session.log.info("Setting copr build {} to {}"
                         .format(build.copr_build_id, state))
        if session.db.query(CoprRebuild)\
                .filter_by(request_id=build.request_id)\
                .filter(CoprRebuild.state.notin_(Build.FINISHED_STATES) |
                        (CoprRebuild.state == None))\
                .count() == 0:
            build.request.state = 'finished'
    session.db.commit()


@listen_event('polling_event')
def poll_copr(session):
    session.log.info('Polling copr')
    for build in session.db.query(CoprRebuild).filter_by(state=Build.RUNNING):
        refresh_build_state(session, build)


@listen_event('fedmsg_event')
def process_fedmsg(session, topic, msg):
    if (topic == get_config('copr.fedmsg_topic') and
            msg['msg']['user'] == get_config('copr.copr_owner')):
        rebuild = session.db.query(CoprRebuild)\
            .filter(CoprRebuild.copr_build_id == int(msg['msg']['build']))\
            .first()
        if rebuild:
            refresh_build_state(session, rebuild)
