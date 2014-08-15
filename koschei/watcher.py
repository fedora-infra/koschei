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
import concurrent.futures
import koji

from . import util, backend
from .service import service_main
from .models import Build, Session, Package
from .dependency import repo_done

log = logging.getLogger('koschei-watcher')

topic_name = util.config['fedmsg']['topic']
tag = util.config['fedmsg']['tag']
instance = util.config['fedmsg']['instance']
build_tag = util.koji_config['target_tag']

def new_repo_entry():
    db_session = Session()
    try:
        repo_done(db_session)
    except:
        db_session.rollback()
        raise
    finally:
        db_session.close()

repo_done_excutor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
eager = util.config['services']['watcher']['eager_repo_done']
repo_done_future = repo_done_excutor.submit(new_repo_entry) if eager else None

def get_topic(name):
    return '{}.{}'.format(topic_name, name)

def consume(db_session, koji_session, topic, content):
    global repo_done_future
    if repo_done_future and repo_done_future.done():
        # This will raise an exception if the thread crashed
        repo_done_future.result()
    if not content.get('instance') == instance:
        return
    log.info('consuming ' + topic)
    if topic == get_topic('task.state.change'):
        update_build_state(db_session, content)
    elif topic == get_topic('repo.done'):
        if content.get('tag') == tag:
            if not repo_done_future or repo_done_future.done():
                repo_done_future = repo_done_excutor.submit(new_repo_entry)
    elif topic == get_topic('build.state.change'):
        register_real_build(db_session, koji_session, content)

def update_build_state(db_session, msg):
    assert msg['attribute'] == 'state'
    task_id = msg['id']
    build = db_session.query(Build).filter_by(task_id=task_id).first()
    if build:
        state = msg['new']
        backend.update_build_state(db_session, build, state)
    db_session.close()

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
            db_session.commit()

@service_main()
def main(db_session, koji_session):
    for _, _, topic, msg in fedmsg.tail_messages():
        if topic.startswith(topic_name + '.'):
            consume(db_session, koji_session, topic, msg['msg'])
