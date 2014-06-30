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
import fedmsg.consumers
import logging

from .models import Build, Session, Package
from .submitter import update_koji_state
from .dependency import repo_done

log = logging.getLogger('koschei-watcher')

topic_name = 'org.fedoraproject.prod.buildsys'

class KojiWatcher(fedmsg.consumers.FedmsgConsumer):
    topic = '{}.*'.format(topic_name)
    config_key = 'koschei.koji-watcher'

    def __init__(self, *args, **kwargs):
        super(KojiWatcher, self).__init__(*args, **kwargs)
        print 'Watcher initialized'

    def consume(self, msg):
        topic = msg['topic']
        content = msg['body']['msg']
        consume(topic, content)

def consume(topic, content):
    if not content.get('instance') == 'primary':
        return
    if topic == 'org.fedoraproject.prod.buildsys.task.state.change':
        update_build_state(content)
    elif topic == 'org.fedoraproject.prod.buildsys.repo.done':
        if content.get('tag') == 'f21-build':
            session = Session()
            repo_done()
            session.close()

def update_build_state(msg):
    assert msg['attribute'] == 'state'
    session = Session()
    task_id = msg['id']
    build = session.query(Build).filter_by(task_id=task_id).first()
    if build:
        state = msg['new']
        update_koji_state(session, build, state)
    session.close()

if __name__ == '__main__':
    print('watcher started')
    for _, _, topic, msg in fedmsg.tail_messages():
        if topic.startswith(topic_name + '.'):
            consume(topic, msg['msg'])
