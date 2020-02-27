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

import json
import jsonschema
from fedora_messaging.api import Message
from unittest.mock import patch
from test import testdir
from test.common import DBTest, with_koji_cassette
from mock import Mock, ANY, call
from koschei import plugin
from koschei.models import Build, Collection, BuildGroup
from koschei.plugins.osci_plugin.backend import (
    poll_osci_artifacts, get_artifact, get_queued_message, get_running_message,
    get_passed_message, get_failed_message, get_aborted_message,
    collection_has_running_build, collection_has_schedulable_package, collection_has_broken_package
)


def validate_schema(obj, schema_ref):
    schema_path = f'{testdir}/schema/{schema_ref}.json'
    with open(schema_path) as f:
        schema = json.load(f)
    jsonschema.validate(obj, schema, resolver=jsonschema.RefResolver(f'file://{schema_path}', None))


class OSCIUtilTest(DBTest):
    @with_koji_cassette
    def test_artifact_empty(self):
        artifact = get_artifact(self.session, 1041751, 'f30-kde')
        validate_schema(artifact, 'rpm-build-group')
        expected = {'type': 'koji-build-group',
                    'builds': [],
                    'repository': 'https://primary-koji.test/repos/f30-kde/1041751/x86_64',
                    'id': 'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}
        self.assertDictEqual(expected, artifact)

    @with_koji_cassette
    def test_artifact_singleton(self):
        artifact = get_artifact(self.session, 1041788, 'f30-kde')
        validate_schema(artifact, 'rpm-build-group')
        expected = {'type': 'koji-build-group',
                    'builds': [{'type': 'koji-build',
                                'id': 1170640,
                                'issuer': 'rdieter',
                                'component': 'qt5-qtbase',
                                'nvr': 'qt5-qtbase-5.11.3-1.fc30',
                                'scratch': False}],
                    'repository': 'https://primary-koji.test/repos/f30-kde/1041788/x86_64',
                    'id': 'sha256:3f1d00be3daa508db0366bfa154445ea02ce1949f7d375d9bab13b65e4954edc'}
        self.assertDictEqual(expected, artifact)

    @with_koji_cassette
    def test_artifact_two(self):
        artifact = get_artifact(self.session, 1041857, 'f30-kde')
        validate_schema(artifact, 'rpm-build-group')
        self.assertEqual(2, len(artifact['builds']))
        self.assertEqual('sha256:7f16732fc11cd6a8744cc7ede57b2b9fb93a1a7569f2d64b828879602287d536', artifact['id'])

    @with_koji_cassette
    def test_message_queued(self):
        msg = get_queued_message(self.session, 1041788, 'f30-kde')
        validate_schema(msg, 'koji-build-group.test.queued')

    @with_koji_cassette
    def test_message_running(self):
        msg = get_running_message(self.session, 1041788, 'f30-kde')
        validate_schema(msg, 'koji-build-group.test.running')

    @with_koji_cassette
    def test_message_passed(self):
        msg = get_passed_message(self.session, 1041788, 'f30-kde')
        validate_schema(msg, 'koji-build-group.test.complete')

    @with_koji_cassette
    def test_message_failed(self):
        msg = get_failed_message(self.session, 1041788, 'f30-kde')
        validate_schema(msg, 'koji-build-group.test.complete')

    @with_koji_cassette
    def test_message_aborted(self):
        msg = get_aborted_message(self.session, 1041788, 'f30-kde')
        validate_schema(msg, 'koji-build-group.test.error')

    def test_running_build(self):
        self.assertFalse(collection_has_running_build(self.db, self.collection))
        b = self.prepare_build('foo')
        self.assertTrue(collection_has_running_build(self.db, self.collection))
        b.state = Build.FAILED
        self.db.commit()
        self.assertFalse(collection_has_running_build(self.db, self.collection))
        self.prepare_build('bar')
        self.prepare_build('baz')
        self.assertTrue(collection_has_running_build(self.db, self.collection))

    def test_schedulable_package(self):
        self.assertFalse(collection_has_schedulable_package(self.db, self.collection))
        p = self.prepare_package('foo', resolved=True)
        self.assertFalse(collection_has_schedulable_package(self.db, self.collection))
        self.prepare_build('foo', state=True)
        self.assertTrue(collection_has_schedulable_package(self.db, self.collection))
        p.manual_priority = -1000
        self.db.commit()
        self.assertFalse(collection_has_schedulable_package(self.db, self.collection))
        b = self.prepare_build('bar', state=True)
        self.assertFalse(collection_has_schedulable_package(self.db, self.collection))
        b.package.resolved = True
        self.db.commit()
        self.assertTrue(collection_has_schedulable_package(self.db, self.collection))

    def test_collections_diff(self):
        base_id = self.collection.id
        self.prepare_build('good', True)
        self.prepare_build('bad', False)
        self.prepare_build('broken', True)
        self.prepare_build('fixed', False)
        self.db.commit()
        self.collection = Collection(
            name="epel7", display_name="EPEL 7", target="epel7",
            dest_tag='epel7', build_tag="epel7-build", priority_coefficient=0.2,
            latest_repo_resolved=False, latest_repo_id=456,
        )
        self.db.add(self.collection)
        self.db.commit()
        self.prepare_build('good', True)
        self.prepare_build('bad', False)
        self.prepare_build('fixed', True)
        self.assertFalse(collection_has_broken_package(self.db, self.collection.id, base_id))
        self.prepare_build('broken', False)
        self.assertTrue(collection_has_broken_package(self.db, self.collection.id, base_id))


class OSCIPluginTest(DBTest):
    def setUp(self):
        super(OSCIPluginTest, self).setUp()
        plugin.load_plugins('backend', ['osci'])

    def koji(self, koji_id):
        koji_mock = Mock()
        koji_mock.repoInfo = Mock(return_value={'create_event': 123, 'tag_name': 'tt'})
        koji_mock.listTagged = Mock(return_value=[{'id': 1, 'owner_name': 'jdoe', 'package_name': 'foo', 'nvr': 'foo-1.2.3-4'}])
        return koji_mock

    def test_osci_polling(self):
        side = self.prepare_collection('f31-side-kde', target='f31-kde', build_tag='f31-kde', dest_tag='f31-kde',
                                       latest_repo_id=None, latest_repo_resolved=None, priority_coefficient=0)
        bp = self.prepare_package('rnv', resolved=True)
        sp = self.prepare_package('rnv', side, resolved=True)
        self.prepare_build(bp, True)
        self.prepare_build(sp, True)
        a = BuildGroup(collection_id=side.id, base_collection_id=self.collection.id)
        self.db.add(a)
        self.db.commit()

        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(None, a.repo_id)
            self.assertEqual(None, a.state)
            fedmsg_mock.assert_not_called()

        side.latest_repo_id = 123
        side.latest_repo_resolved = False
        self.db.commit()
        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(123, a.repo_id)
            self.assertEqual('failed', a.state)
            fedmsg_mock.assert_called_once_with(Message(topic='ci.osci.koji-build-group.test.complete', body=ANY))

        sp.manual_priority = 1000
        side.latest_repo_id = 456
        side.latest_repo_resolved = True
        self.db.commit()
        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(456, a.repo_id)
            self.assertEqual('running', a.state)
            fedmsg_mock.assert_has_calls((call(Message(topic='ci.osci.koji-build-group.test.error', body=ANY)),
                                     call(Message(topic='ci.osci.koji-build-group.test.running', body=ANY))))

        sp.manual_priority = 0
        self.db.commit()
        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(456, a.repo_id)
            self.assertEqual('passed', a.state)
            fedmsg_mock.assert_called_once_with(Message(topic='ci.osci.koji-build-group.test.complete', body=ANY))

        b = self.prepare_build(sp, None)
        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(456, a.repo_id)
            self.assertEqual('running', a.state)
            fedmsg_mock.assert_called_once_with(Message(topic='ci.osci.koji-build-group.test.running', body=ANY))

        b.state = Build.FAILED
        self.db.commit()
        with patch('fedora_messaging.api.publish') as fedmsg_mock:
            poll_osci_artifacts(self.session)
            self.assertEqual(456, a.repo_id)
            self.assertEqual('failed', a.state)
            fedmsg_mock.assert_called_once_with(Message(topic='ci.osci.koji-build-group.test.complete', body=ANY))
