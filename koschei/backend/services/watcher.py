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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import fedmsg
import requests

from koschei import plugin, backend
from koschei.config import get_config
from koschei.backend import service
from koschei.backend.service import Service
from koschei.models import Build, Package


class Watcher(Service):
    def __init__(self, session):
        super(Watcher, self).__init__(session)

    def get_topic(self, name):
        return '{}.{}'.format(get_config('fedmsg.topic'), name)

    def consume(self, topic, msg):
        content = msg['msg']
        if content.get('instance') == get_config('fedmsg.instance'):
            self.log.debug('consuming ' + topic)
            if topic == self.get_topic('task.state.change'):
                self.update_build_state(content)
            elif topic == self.get_topic('tag'):
                self.register_real_build(content)

    def update_build_state(self, msg):
        assert msg['attribute'] == 'state'
        task_id = msg['id']
        build = self.db.query(Build).filter_by(task_id=task_id).first()
        if build:
            state = msg['new']
            backend.update_build_state(self.session, build, state)

    def register_real_build(self, msg):
        pkg = self.db.query(Package).filter_by(name=msg['name']).first()
        if pkg:
            newer_build = backend.get_newer_build_if_exists(self.session, pkg)
            if newer_build:
                backend.register_real_builds(
                    self.session,
                    pkg.collection,
                    [(pkg.id, newer_build)],
                )

    def notify_watchdog(self):
        if not get_config('services.watcher.watchdog'):
            return
        service.sd_notify("WATCHDOG=1")

    def main(self):
        try:
            for _, _, topic, msg in fedmsg.tail_messages():
                self.notify_watchdog()
                try:
                    if topic.startswith(get_config('fedmsg.topic') + '.'):
                        self.consume(topic, msg)
                    plugin.dispatch_event('fedmsg_event', self.session, topic, msg)
                finally:
                    self.db.rollback()
        except requests.exceptions.ConnectionError:
            self.log.exception("Fedmsg watcher exception.")
            fedmsg.destroy()
            fedmsg.init()
