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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import fedmsg
import requests

from . import util, plugin
from .backend import Backend
from .service import KojiService
from .models import Build, Package


class Watcher(KojiService):

    topic_name = util.config['fedmsg']['topic']
    instance = util.config['fedmsg']['instance']
    watchdog = util.config['services']['watcher']['watchdog']

    def __init__(self, backend=None, *args, **kwargs):
        super(Watcher, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log, db=self.db,
                                          koji_sessions=self.koji_sessions)

    def get_topic(self, name):
        return '{}.{}'.format(self.topic_name, name)

    def consume(self, topic, msg):
        content = msg['msg']
        if content.get('instance') == self.instance:
            self.log.info('consuming ' + topic)
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
            self.backend.update_build_state(build, state)

    def register_real_build(self, msg):
        pkg = self.db.query(Package).filter_by(name=msg['name']).first()
        if pkg:
            newer_build = self.backend.get_newer_build_if_exists(pkg)
            if newer_build:
                self.backend.register_real_build(pkg, newer_build)

    def notify_watchdog(self):
        if not self.watchdog:
            return
        util.sd_notify("WATCHDOG=1")

    def main(self):
        try:
            for _, _, topic, msg in fedmsg.tail_messages():
                self.notify_watchdog()
                try:
                    if topic.startswith(self.topic_name + '.'):
                        self.consume(topic, msg)
                    plugin.dispatch_event('fedmsg_event', topic, msg, db=self.db,
                                          koji_sessions=self.koji_sessions)
                finally:
                    self.db.rollback()
        except requests.exceptions.ConnectionError:
            self.log.exception("Fedmsg watcher exception.")
            fedmsg.destroy()
            fedmsg.init()
