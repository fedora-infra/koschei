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

from mock import Mock, patch

from test.common import DBTest, service_ctor
from test.koji_data import *
from koschei.models import KojiTask

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

    def test_real_build(self):
        self.session.koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.repo_id = 1
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        self.session.koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        msg = {
            'msg': {
                'name': 'rnv',
                'owner': 'msimacek',
                'release': '10.fc24',
                'tag': 'f25',
                'tag_id': 335,
                'user': 'msimacek',
                'instance': 'primary',
                'version': '1.7.11',
            }
        }
        topic = test_topic + '.tag'
        Watcher(self.session).consume(topic, msg)
        self.assertEqual('ok', package.state_string)
        self.assertEqual(460889, package.last_complete_build.repo_id)
        self.assertCountEqual([(x['id'],) for x in rnv_subtasks],
                             self.db.query(KojiTask.task_id))
