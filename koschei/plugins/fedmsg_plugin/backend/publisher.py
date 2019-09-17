# Copyright (C) 2014-2019  Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import fedora_messaging.api as fedmsg

from koschei.config import get_config
from koschei.plugin import listen_event


def publish_fedmsg(session, message):
    if not get_config('fedmsg-publisher.enabled', False):
        return
    message = fedmsg.Message(
        topic='{modname}.{topic}'.format(**message),
        body=message['msg'],
    )
    session.log.info('Publishing fedmsg:\n' + str(message))
    fedmsg.publish(message)


@listen_event('package_state_change')
def emit_package_state_update(session, package, prev_state, new_state):
    if prev_state == new_state:
        return
    group_names = [group.full_name for group in package.groups]
    message = dict(
        topic='package.state.change',
        modname=get_config('fedmsg-publisher.modname'),
        msg=dict(
            name=package.name,
            old=prev_state,
            new=new_state,
            koji_instance=get_config('fedmsg.instance'),
            repo=package.collection.name,  # compat only, same as collection
            collection=package.collection.name,
            collection_name=package.collection.display_name,
            groups=group_names,
        ),
    )
    publish_fedmsg(session, message)


@listen_event('collection_state_change')
def emit_collection_state_update(session, collection, prev_state, new_state):
    if prev_state == new_state:
        return
    message = dict(
        topic='collection.state.change',
        modname=get_config('fedmsg-publisher.modname'),
        msg=dict(
            old=prev_state,
            new=new_state,
            koji_instance=get_config('fedmsg.instance'),
            collection=collection.name,
            collection_name=collection.display_name,
            repo_id=collection.latest_repo_id,
        ),
    )
    publish_fedmsg(session, message)
