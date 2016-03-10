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
import logging

from koschei.util import config
from koschei.plugin import listen_event

log = logging.getLogger('koschei.fedmsg_publisher')

fedmsg_config = config['fedmsg-publisher']

if fedmsg_config['enabled']:
    @listen_event('package_state_change')
    def emit_package_state_update(package, prev_state, new_state):
        if prev_state == new_state:
            return
        group_names = [group.full_name for group in package.groups]
        message = dict(topic='package.state.change',
                       modname=fedmsg_config['modname'],
                       msg={'name': package.name,
                            'old': prev_state,
                            'new': new_state,
                            'koji_instance': config['fedmsg']['instance'],
                            # compat
                            'repo': package.collection.target_tag,
                            'collection': package.collection.name,
                            'collection_name': package.collection.display_name,
                            'groups': group_names})
        log.info('Publishing fedmsg:\n' + str(message))
        fedmsg.publish(**message)
