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

from . import util
from .models import Build, Session
from .submitter import update_koji_state
from .dependency import repo_done

log = logging.getLogger('koschei-watcher')

topic_name = util.config['fedmsg']['topic']
tag = util.config['fedmsg']['tag']
instance = util.config['fedmsg']['tag']

def get_topic(name):
    return '{}.{}'.format(topic_name, name)

def consume(topic, content):
    log.info('consuming ' + topic)
    if not content.get('instance') == instance:
        return
    if topic == get_topic('task.state.change'):
        update_build_state(content)
    elif topic == get_topic('repo.done'):
        if content.get('tag') == tag:
            session = Session()
            repo_done(session)
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

def main():
    print('watcher started')
    for _, _, topic, msg in fedmsg.tail_messages():
        if topic.startswith(topic_name + '.'):
            consume(topic, msg['msg'])

if __name__ == '__main__':
    main()
