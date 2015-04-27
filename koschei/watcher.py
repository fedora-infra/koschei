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

from signal import signal, alarm, SIGALRM

from . import util, plugin
from .backend import Backend
from .service import KojiService, FedmsgService, Service
from .models import Build, Package, RepoGenerationRequest

class WatchdogInterrupt(Exception):
    pass

class WatchdogService(Service):
    def get_handled_exceptions(self):
        return (list([WatchdogInterrupt]) +
                super(WatchdogService, self).get_handled_exceptions())

class Watcher(KojiService, FedmsgService, WatchdogService):

    topic_name = util.config['fedmsg']['topic']
    tag = util.koji_config['build_tag']
    instance = util.config['fedmsg']['instance']
    build_tag = util.koji_config['target_tag']
    watchdog_interval = util.config['services']['watcher']['watchdog_interval']

    def __init__(self, backend=None, *args, **kwargs):
        super(Watcher, self).__init__(*args, **kwargs)
        self.backend = backend or Backend(log=self.log,
                                          db=self.db,
                                          koji_session=self.koji_session)

    def get_topic(self, name):
        return '{}.{}'.format(self.topic_name, name)

    def consume(self, topic, msg):
        content = msg['msg']
        if not content.get('instance') == self.instance:
            return
        self.log.info('consuming ' + topic)
        if topic == self.get_topic('task.state.change'):
            self.update_build_state(content)
        elif topic == self.get_topic('repo.done'):
            if content.get('tag') == self.tag:
                self.repo_done(content['repo_id'])
        elif topic == self.get_topic('build.tag'):
            self.register_real_build(content)
        plugin.dispatch_event('fedmsg_event', topic, msg, db=self.db,
                              koji_session=self.koji_session)

    def repo_done(self, repo_id):
        request = RepoGenerationRequest(repo_id=repo_id)
        self.db.add(request)
        self.db.commit()

    def update_build_state(self, msg):
        assert msg['attribute'] == 'state'
        task_id = msg['id']
        build = self.db.query(Build).filter_by(task_id=task_id).first()
        if build:
            state = msg['new']
            self.backend.update_build_state(build, state)

    def register_real_build(self, msg):
        if msg['tag'] == self.tag:
            pkg = self.db.query(Package).filter_by(name=msg['name']).first()
            if pkg:
                newer_build = self.backend.get_newer_build_if_exists(pkg)
                if newer_build:
                    self.backend.register_real_build(pkg, newer_build)
                    self.db.commit()

    def main(self):
        def handler(n, s):
            raise WatchdogInterrupt("Watchdog timeout")
        signal(SIGALRM, handler)
        alarm(self.watchdog_interval)
        for _, _, topic, msg in self.fedmsg.tail_messages():
            if topic.startswith(self.topic_name + '.'):
                self.consume(topic, msg)
            alarm(self.watchdog_interval)
