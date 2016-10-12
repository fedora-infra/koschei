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

from test.common import DBTest, KojiMock
from test.koji_data import *
from koschei.backend.services.watcher import Watcher
from koschei.models import KojiTask

test_topic = 'org.fedoraproject.test.buildsys'


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
        def tail_messages_mock():
            yield ('', '', 'org.fedoraproject.prod.buildsys.task.state.change',
                   generate_state_change())
        with patch('fedmsg.tail_messages', tail_messages_mock):
            Watcher(db=Mock(),
                    koji_sessions={'primary': Mock(), 'secondary': Mock()}).main()

    def test_ignored_instance(self):
        def tail_messages_mock():
            yield ('', '', test_topic + '.task.state.change',
                   generate_state_change(instance='ppc'))
        with patch('fedmsg.tail_messages', tail_messages_mock):
            Watcher(db=Mock(),
                    koji_sessions={'primary': Mock(), 'secondary': Mock()}).main()

    def test_task_completed(self):
        def tail_messages_mock():
            yield ('', '', test_topic + '.task.state.change',
                   generate_state_change())
        _, build = self.prepare_basic_data()
        backend_mock = Mock()
        with patch('fedmsg.tail_messages', tail_messages_mock):
            Watcher(db=self.db,
                    koji_sessions={'primary': Mock(), 'secondary': Mock()},
                    backend=backend_mock).main()
            backend_mock.update_build_state.assert_called_once_with(build, 'CLOSED')

    def test_real_build(self):
        koji_mock = KojiMock()
        koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.repo_id = 1
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        koji_mock.listTagged = Mock(return_value=rnv_build_info)
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
        def tail_messages_mock():
            yield '', '', test_topic + '.tag', msg
        with patch('fedmsg.tail_messages', tail_messages_mock):
            Watcher(db=self.db,
                    koji_sessions={'primary': koji_mock, 'secondary': Mock()}).main()
        self.assertEqual('ok', package.state_string)
        self.assertEquals(460889, package.last_complete_build.repo_id)
        self.assertItemsEqual([(x['id'],) for x in rnv_subtasks],
                              self.db.query(KojiTask.task_id))
