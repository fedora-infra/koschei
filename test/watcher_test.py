# Copyright (C) 2014-2016 Red Hat, Inc.
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

from mock import patch

from test.common import DBTest, service_ctor, with_koji_cassette

test_topic = 'org.fedoraproject.test.buildsys'

Watcher = service_ctor('watcher', plugin_name='fedmsg')


def generate_state_change(instance='primary', task_id=666, old='OPEN', new='CLOSED'):
    return {
        'msg': {
            'instance': instance,
            'attribute': 'state',
            'id': task_id,
            'old': old,
            'new': new,
        }
    }


class WatcherTest(DBTest):
    def test_ignored_topic(self):
        topic = 'org.fedoraproject.prod.buildsys.task.state.change'
        msg = generate_state_change()
        Watcher(self.session).consume(topic, msg)

    def test_ignored_instance(self):
        topic = test_topic + '.task.state.change'
        msg = generate_state_change(instance='ppc')
        Watcher(self.session).consume(topic, msg)

    def test_task_completed(self):
        topic = test_topic + '.task.state.change'
        msg = generate_state_change()
        _, build = self.prepare_basic_data()
        with patch('koschei.backend.update_build_state') as update_mock:
            Watcher(self.session).consume(topic, msg)
            update_mock.assert_called_once_with(self.session, build, 'CLOSED')

    @with_koji_cassette
    def test_real_build(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        build = self.prepare_build(
            package, 'failed', version='1.7.11', release='14.fc28',
            task_id=25038558, started='2018-02-14 11:16:55',
        )
        msg = {
            'msg': {
                "build_id": 1046486,
                "name": "rnv",
                "tag_id": 3418,
                "instance": "primary",
                "tag": "f29",
                "user": "mohanboddu",
                "version": "1.7.11",
                "owner": "msimacek",
                "release": "15.fc28"
            }
        }
        topic = test_topic + '.tag'
        Watcher(self.session).consume(topic, msg)
        self.assertEqual('ok', package.state_string)
        self.assertIsNot(build, package.last_complete_build)
        self.assertEqual(859626, package.last_complete_build.repo_id)
        self.assertEqual(25162638, package.last_complete_build.task_id)
        self.assertEqual(7, len(package.last_complete_build.build_arch_tasks))
