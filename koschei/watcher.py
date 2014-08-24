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
import logging
import koji

from . import util, backend
from .service import service_main
from .models import Build, Package, RepoGenerationRequest

log = logging.getLogger('koschei-watcher')

topic_name = util.config['fedmsg']['topic']
tag = util.config['fedmsg']['tag']
instance = util.config['fedmsg']['instance']
build_tag = util.koji_config['target_tag']

def get_topic(name):
    return '{}.{}'.format(topic_name, name)

def consume(db_session, koji_session, topic, content):
    if not content.get('instance') == instance:
        return
    log.info('consuming ' + topic)
    if topic == get_topic('task.state.change'):
        update_build_state(db_session, content)
    elif topic == get_topic('repo.done'):
        if content.get('tag') == tag:
            repo_done(db_session, content['repo_id'])
    elif topic == get_topic('build.state.change'):
        register_real_build(db_session, koji_session, content)

def repo_done(db_session, repo_id):
    request = RepoGenerationRequest(repo_id=repo_id)
    db_session.add(request)
    db_session.commit()

def update_build_state(db_session, msg):
    assert msg['attribute'] == 'state'
    task_id = msg['id']
    build = db_session.query(Build).filter_by(task_id=task_id).first()
    if build:
        state = msg['new']
        backend.update_build_state(db_session, build, state)

def register_real_build(db_session, koji_session, msg):
    assert msg['attribute'] == 'state'
    if msg['new'] == koji.BUILD_STATES['COMPLETE']:
        name = msg['name']
        pkg = db_session.query(Package).filter_by(name=name).first()
        if not pkg:
            return
        last_builds = koji_session.getLatestBuilds(build_tag, package=name)
        if not last_builds:
            return
        last_build = last_builds[0]
        if last_build['build_id'] == msg['build_id']:
            log.info("Registering real build {nvr}".format(nvr=last_build['nvr']))
            build = Build(package_id=pkg.id, version=last_build['version'],
                          release=last_build['release'], epoch=last_build['epoch'],
                          state=Build.COMPLETE, real=True,
                          started=util.parse_koji_time(last_build['creation_time']),
                          task_id=last_build['task_id'])
            db_session.add(build)
            db_session.flush()
            backend.build_registered(db_session, build)
            db_session.commit()

@service_main()
def main(db_session, koji_session):
    for _, _, topic, msg in fedmsg.tail_messages():
        if topic.startswith(topic_name + '.'):
            consume(db_session, koji_session, topic, msg['msg'])
