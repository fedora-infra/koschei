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

import fedmsg

from koschei.util import config
from koschei.backend import PackageStateUpdateEvent

fedmsg_config = config['fedmsg-publisher']

if fedmsg_config['enabled']:
    @PackageStateUpdateEvent.listen
    def emit_package_state_update(event):
        if event.prev_state == event.new_state:
            return
        fedmsg.publish(topic='package.state.change',
                       modname=fedmsg_config['modname'],
                       msg={'name': event.package.name,
                            'old': event.prev_state,
                            'new': event.new_state,
                            'koji_instance': config['fedmsg']['instance'],
                            'repo': config['koji_config']['target_tag'],
                            })
