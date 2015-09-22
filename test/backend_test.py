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

import koji
import logging

from common import DBTest, postgres_only
from mock import Mock, patch
from koschei.backend import Backend
from koschei import plugin

rnv_task = {'arch': 'noarch',
            'awaited': None,
            'channel_id': 1,
            'completion_time': '2015-03-01 13:43:25.800364',
            'completion_ts': 1425217405.80036,
            'create_time': '2015-03-01 13:39:38.041833',
            'create_ts': 1425217178.04183,
            'host_id': 63,
            'id': 9107738,
            'label': None,
            'method': 'build',
            'owner': 2645,
            'parent': None,
            'priority': 50,
            'start_time': '2015-03-01 13:39:38.191753',
            'start_ts': 1425217178.19175,
            'state': koji.TASK_STATES['CLOSED'],
            'waiting': False,
            'weight': 0.2}

rnv_subtasks = [{'arch': 'armhfp',
                 'awaited': False,
                 'channel_id': 1,
                 'completion_time': '2015-03-01 13:43:14.307429',
                 'completion_ts': 1425217394.30743,
                 'create_time': '2015-03-01 13:39:39.263872',
                 'create_ts': 1425217179.26387,
                 'host_id': 126,
                 'id': 9107739,
                 'label': 'armv7hl',
                 'method': 'buildArch',
                 'owner': 2645,
                 'parent': 9107738,
                 'priority': 49,
                 'start_time': '2015-03-01 13:39:41.486608',
                 'start_ts': 1425217181.48661,
                 'state': koji.TASK_STATES['CLOSED'],
                 'waiting': None,
                 'weight': 1.64134472187},
                {'arch': 'i386',
                 'awaited': False,
                 'channel_id': 1,
                 'completion_time': '2015-03-01 13:41:54.777992',
                 'completion_ts': 1425217314.77799,
                 'create_time': '2015-03-01 13:39:39.389699',
                 'create_ts': 1425217179.3897,
                 'host_id': 60,
                 'id': 9107741,
                 'label': 'i686',
                 'method': 'buildArch',
                 'owner': 2645,
                 'parent': 9107738,
                 'priority': 49,
                 'start_time': '2015-03-01 13:39:46.519139',
                 'start_ts': 1425217186.51914,
                 'state': koji.TASK_STATES['CLOSED'],
                 'waiting': None,
                 'weight': 1.64134472187},
                {'arch': 'x86_64',
                 'awaited': False,
                 'channel_id': 1,
                 'completion_time': '2015-03-01 13:40:54.104695',
                 'completion_ts': 1425217254.1047,
                 'create_time': '2015-03-01 13:39:39.346411',
                 'create_ts': 1425217179.34641,
                 'host_id': 82,
                 'id': 9107740,
                 'label': 'x86_64',
                 'method': 'buildArch',
                 'owner': 2645,
                 'parent': 9107738,
                 'priority': 49,
                 'start_time': '2015-03-01 13:39:41.574641',
                 'start_ts': 1425217181.57464,
                 'state': koji.TASK_STATES['CLOSED'],
                 'waiting': None,
                 'weight': 1.64134472187}]

inconsistent_subtask = [{'arch': 'armhfp',
                         'awaited': False,
                         'channel_id': 1,
                         'completion_time': None,
                         'completion_ts': None,
                         'create_time': '2015-03-01 13:39:39.263872',
                         'create_ts': 1425217179.26387,
                         'host_id': 126,
                         'id': 9107739,
                         'label': 'armv7hl',
                         'method': 'buildArch',
                         'owner': 2645,
                         'parent': 9107738,
                         'priority': 49,
                         'start_time': '2015-03-01 13:39:41.486608',
                         'start_ts': 1425217181.48661,
                         'state': koji.TASK_STATES['OPEN'],
                         'waiting': None,
                         'weight': 1.64134472187}]

class BackendTest(DBTest):
    def setUp(self):
        super(BackendTest, self).setUp()
        self.koji_session = Mock()
        self.log = Mock()
        self.backend = Backend(db=self.s, koji_session=self.koji_session,
                               log=logging.getLogger('koschei.backend'))
        plugin.load_plugins(['fedmsg_publisher'])


    @postgres_only
    def test_update_state(self):
        self.koji_session.getTaskInfo = Mock(return_value=rnv_task)
        self.koji_session.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        self.prepare_builds(rnv=False)
        running_build = self.prepare_builds(rnv=None)[0]
        running_build.task_id = rnv_task['id']
        self.s.commit()
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.update_build_state(running_build, 'CLOSED')
            self.koji_session.getTaskInfo.assert_called_once_with(rnv_task['id'])
            self.koji_session.getTaskChildren.assert_called_once_with(rnv_task['id'],
                                                                      request=True)
            self.assertEqual('ok', package.state_string)
            event.assert_called_once_with('package_state_change', package=package,
                                          prev_state='failing', new_state='ok')

    # Regression test for https://github.com/msimacek/koschei/issues/27
    @postgres_only
    def test_update_state_inconsistent(self):
        self.koji_session.getTaskInfo = Mock(return_value=rnv_task)
        self.koji_session.getTaskChildren = Mock(return_value=inconsistent_subtask)
        package = self.prepare_packages(['rnv'])[0]
        self.prepare_builds(rnv=False)
        running_build = self.prepare_builds(rnv=None)[0]
        running_build.task_id = rnv_task['id']
        self.s.commit()
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.update_build_state(running_build, 'CLOSED')
            self.koji_session.getTaskInfo.assert_called_once_with(rnv_task['id'])
            self.koji_session.getTaskChildren.assert_called_once_with(rnv_task['id'],
                                                                      request=True)
            self.assertEqual('ok', package.state_string)
            event.assert_called_once_with('package_state_change', package=package,
                                          prev_state='failing', new_state='ok')
