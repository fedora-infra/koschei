# Copyright (C) 2020 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import datetime
import hashlib
import fedora_messaging.api as fedmsg
from koschei.models import Collection, Build, Package
from koschei.config import get_config, get_koji_config
from koschei.models import BuildGroup
from koschei.plugin import listen_event


def koji_build_to_osci_build(koji_build):
    osci_build = dict()
    osci_build['type'] = get_config('osci.build_artifact_type')
    osci_build['id'] = koji_build['id']
    osci_build['issuer'] = koji_build['owner_name']
    osci_build['component'] = koji_build['package_name']
    osci_build['nvr'] = koji_build['nvr']
    osci_build['scratch'] = False
    return osci_build


def artifact_id_from_builds(builds):
    payload = ''.join(sorted(str(build['id']) for build in builds))
    hexdigest = hashlib.sha256(payload.encode('ascii')).hexdigest()
    return f'sha256:{hexdigest}'


def repo_path(repo_id, repo_tag):
    arch = get_config('dependency.repo_arch')
    topurl = get_koji_config('primary', 'topurl')
    return f'{topurl}/repos/{repo_tag}/{repo_id}/{arch}'


def get_artifact(session, repo_id, dest_tag):
    koji_session = session.koji('primary')
    repo = koji_session.repoInfo(repo_id)
    event_id = repo['create_event']
    builds = koji_session.listTagged(dest_tag, event_id, latest=True)
    artifact = dict()
    artifact['type'] = get_config('osci.build_group_artifact_type')
    artifact['builds'] = [koji_build_to_osci_build(b) for b in builds]
    artifact['repository'] = repo_path(repo_id, repo['tag_name'])
    artifact['id'] = artifact_id_from_builds(artifact['builds'])
    return artifact


def _get_incomplete_message(session, repo_id, dest_tag):
    return {
        'version': '0.2.5',
        'generated_at': datetime.datetime.utcnow().isoformat() + 'Z',
        'contact': get_config('osci.contact'),
        'pipeline': get_config('osci.pipeline'),
        'test': get_config('osci.test'),
        'run': {
            'url': 'url',
            'log': 'log',
        },
        'artifact': get_artifact(session, repo_id, dest_tag),
    }


def get_queued_message(session, repo_id, dest_tag):
    return _get_incomplete_message(session, repo_id, dest_tag)


def get_running_message(session, repo_id, dest_tag):
    return _get_incomplete_message(session, repo_id, dest_tag)


def get_aborted_message(session, repo_id, dest_tag):
    msg = _get_incomplete_message(session, repo_id, dest_tag)
    msg['error'] = {'reason': 'Test was aborted by Koschei'}
    return msg


def _get_complete_message(session, repo_id, dest_tag, outcome):
    msg = _get_incomplete_message(session, repo_id, dest_tag)
    msg['system'] = []
    msg['test']['result'] = outcome
    return msg


def get_passed_message(session, repo_id, dest_tag):
    return _get_complete_message(session, repo_id, dest_tag, 'passed')


def get_failed_message(session, repo_id, dest_tag):
    return _get_complete_message(session, repo_id, dest_tag, 'failed')


def collection_has_running_build(db, collection):
    return db.query(Build) \
        .join(Build.package) \
        .filter(Package.collection_id == collection.id) \
        .filter(Build.state == Build.RUNNING) \
        .limit(1).count()


def collection_has_schedulable_package(db, collection):
    priority_threshold = get_config('priorities.build_threshold')
    priority_expr = Package.current_priority_expression(Collection, Build)
    return db.query(Package) \
        .join(Package.collection) \
        .join(Package.last_build) \
        .filter(Package.collection_id == collection.id) \
        .filter(priority_expr != None) \
        .filter(priority_expr >= priority_threshold) \
        .limit(1).count()


def collection_has_broken_package(db, side_collection_id, base_collection_id):
    good_base = db.query(Package.name)\
        .filter(Package.collection_id == base_collection_id)\
        .filter(Package.state_string == 'ok')\
        .all()
    bad_side = db.query(Package.name)\
        .filter(Package.collection_id == side_collection_id)\
        .filter(Package.state_string != 'ok')\
        .all()
    broken = set(good_base) & set(bad_side)
    return len(broken)


def _do_publish(session, topic, body):
    namespace = get_config('osci.namespace')
    artifact_type = get_config('osci.build_group_artifact_type')
    message = fedmsg.Message(
        topic=f'ci.{namespace}.{artifact_type}.test.{topic}',
        body=body,
    )
    session.log.info('Publishing fedmsg:\n' + str(message))
    fedmsg.publish(message)


def _emit_message(session, state, repo_id, dest_tag):
    msg_gen = {
        'queued': get_queued_message,
        'running': get_running_message,
        'passed': get_passed_message,
        'failed': get_failed_message,
        'aborted': get_aborted_message,
    }
    msg = msg_gen[state](session, repo_id, dest_tag)
    topic_map = {
        'queued': 'queued',
        'running': 'running',
        'passed': 'complete',
        'failed': 'complete',
        'aborted': 'error',
    }
    if msg['artifact']['builds']:
        _do_publish(session, topic_map[state], msg)


def _process_artifact(session, a):
    prev_state = a.state
    prev_repo_id = a.repo_id
    c = a.collection
    repo_id = c.latest_repo_id
    if repo_id is None:
        state = None
    elif not c.latest_repo_resolved:
        state = 'failed'
    elif collection_has_running_build(session.db, c):
        state = 'running'
    elif collection_has_schedulable_package(session.db, c):
        state = 'running'
    elif collection_has_broken_package(session.db, a.collection_id, a.base_collection_id):
        state = 'failed'
    else:
        state = 'passed'
    if prev_state != state or prev_repo_id != repo_id:
        if prev_repo_id and prev_repo_id != repo_id:
            _emit_message(session, 'aborted', prev_repo_id, c.dest_tag)
        _emit_message(session, state, repo_id, c.dest_tag)
        a.state = state
        a.repo_id = repo_id
        session.db.commit()


@listen_event('polling_event')
def poll_osci_artifacts(session):
    for build_group in session.db.query(BuildGroup).all():
        _process_artifact(session, build_group)
