# Copyright (C) 2014-2016  Red Hat, Inc.
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

import datetime
import koji
import logging

from copy import deepcopy

from test.common import DBTest, KojiMock
from mock import Mock, patch
from koschei.backend import Backend
from koschei import plugin, models as m
from koschei.models import *

# pylint: disable=unbalanced-tuple-unpacking,blacklisted-name

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
                 'request': ['../packages/rnv/1.7.11/7.fc22/src/rnv-1.7.11-7.fc22.src.rpm',
                             299,
                             'armv7hl',
                             True,
                             {'repo_id': 460889}],
                 'start_time': '2015-03-01 13:39:41.486608',
                 'start_ts': 1425217181.48661,
                 'state': 2,
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
                 'request': ['../packages/rnv/1.7.11/7.fc22/src/rnv-1.7.11-7.fc22.src.rpm',
                             299,
                             'i686',
                             False,
                             {'repo_id': 460889}],
                 'start_time': '2015-03-01 13:39:46.519139',
                 'start_ts': 1425217186.51914,
                 'state': 2,
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
                 'request': ['../packages/rnv/1.7.11/7.fc22/src/rnv-1.7.11-7.fc22.src.rpm',
                             299,
                             'x86_64',
                             False,
                             {'repo_id': 460889}],
                 'start_time': '2015-03-01 13:39:41.574641',
                 'start_ts': 1425217181.57464,
                 'state': 2,
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

rnv_build_info = [{'build_id': 730661,
                   'completion_time': '2016-02-05 04:45:30.705758',
                   'creation_event_id': 14616092,
                   'creation_time': '2016-02-05 04:34:41.128873',
                   'epoch': None,
                   'id': 730661,
                   'name': 'rnv',
                   'nvr': 'rnv-1.7.11-10.fc24',
                   'owner_id': 3445,
                   'owner_name': 'releng',
                   'package_id': 16808,
                   'package_name': 'rnv',
                   'release': '10.fc24',
                   'start_time': '2016-02-05 04:34:41.128873',
                   'state': 1,
                   'tag_id': 308,
                   'tag_name': 'f24',
                   'task_id': 12865126,
                   'version': '1.7.11',
                   'volume_id': 0,
                   'volume_name': 'DEFAULT'}]

package_list = [{'blocked': False,
                 'extra_arches': None,
                 'owner_id': 837,
                 'owner_name': 'akurtakov',
                 'package_id': 183,
                 'package_name': 'eclipse',
                 'tag_id': 335,
                 'tag_name': 'f25'},
                {'blocked': True,
                 'extra_arches': None,
                 'owner_id': 1758,
                 'owner_name': 'jcapik',
                 'package_id': 10326,
                 'package_name': 'maven-doxia-tools',
                 'tag_id': 335,
                 'tag_name': 'f25'},
                {'blocked': False,
                 'extra_arches': None,
                 'owner_id': 837,
                 'owner_name': 'akurtakov',
                 'package_id': 11290,
                 'package_name': 'maven',
                 'tag_id': 335,
                 'tag_name': 'f25'},
                {'blocked': False,
                 'extra_arches': None,
                 'owner_id': 2645,
                 'owner_name': 'msimacek',
                 'package_id': 16808,
                 'package_name': 'rnv',
                 'tag_id': 335,
                 'tag_name': 'f25'}]


class BackendTest(DBTest):
    def setUp(self):
        super(BackendTest, self).setUp()
        self.koji_session = KojiMock()
        self.secondary_koji = KojiMock()
        self.log = Mock()
        self.collection.secondary_mode = True
        self.s.commit()
        self.backend = Backend(db=self.s, koji_sessions={'primary': self.koji_session,
                                                         'secondary': self.secondary_koji},
                               log=logging.getLogger('koschei.backend'))
        plugin.load_plugins('backend', ['fedmsg_publisher'])


    def test_update_state(self):
        self.koji_session.getTaskInfo = Mock(return_value=rnv_task)
        self.koji_session.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
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
            self.assertItemsEqual([(x['id'],) for x in rnv_subtasks],
                                  self.s.query(m.KojiTask.task_id))

    def test_update_state_existing_task(self):
        self.koji_session.getTaskInfo = Mock(return_value=rnv_task)
        self.koji_session.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
        running_build.task_id = rnv_task['id']
        koji_task = m.KojiTask(task_id=rnv_subtasks[0]['id'],
                               arch='noarch',
                               state=koji.TASK_STATES['OPEN'],
                               build_id=running_build.id)
        self.s.add(koji_task)
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
            self.assertItemsEqual([(x['id'],) for x in rnv_subtasks],
                                  self.s.query(m.KojiTask.task_id))

    # Regression test for https://github.com/msimacek/koschei/issues/27
    def test_update_state_inconsistent(self):
        self.koji_session.getTaskInfo = Mock(return_value=rnv_task)
        self.koji_session.getTaskChildren = Mock(return_value=inconsistent_subtask)
        package = self.prepare_packages(['rnv'])[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
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

    def test_refresh_latest_builds(self):
        self.secondary_koji.getTaskInfo = Mock(return_value=rnv_task)
        self.secondary_koji.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        build = self.prepare_build('rnv', False)
        build.repo_id = 1
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        self.secondary_koji.listTagged = Mock(return_value=rnv_build_info)
        self.s.commit()
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.refresh_latest_builds()
            self.secondary_koji.getTaskInfo.assert_called_once_with(rnv_build_info[0]['task_id'])
            self.secondary_koji.getTaskChildren.assert_called_once_with(rnv_build_info[0]['task_id'],
                                                                        request=True)
            self.assertEqual('ok', package.state_string)
            self.assertEquals(460889, package.last_complete_build.repo_id)
            # event.assert_called_once_with('package_state_change', package=package,
            #                               prev_state='failing', new_state='ok')
            self.assertItemsEqual([(x['id'],) for x in rnv_subtasks],
                                  self.s.query(m.KojiTask.task_id))

    def test_refresh_latest_builds_already_present(self):
        self.secondary_koji.getTaskInfo = Mock(return_value=rnv_task)
        self.secondary_koji.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        build = self.prepare_build('rnv', False)
        build.real = True
        build.repo_id = 460889
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        build.task_id = rnv_build_info[0]['task_id']
        self.secondary_koji.listTagged = Mock(return_value=rnv_build_info)
        self.s.commit()
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.refresh_latest_builds()
            self.assertEquals(1, self.s.query(m.Build).count())

    def test_refresh_latest_builds_no_repo_id(self):
        self.secondary_koji.getTaskInfo = Mock(return_value=rnv_task)
        subtasks = deepcopy(rnv_subtasks)
        for subtask in subtasks:
            del subtask['request']
        self.secondary_koji.getTaskChildren = Mock(return_value=subtasks)
        package = self.prepare_packages(['rnv'])[0]
        build = self.prepare_build('rnv', False)
        build.real = True
        build.repo_id = 460889
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        build.task_id = 1234
        self.secondary_koji.listTagged = Mock(return_value=rnv_build_info)
        self.s.commit()
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.refresh_latest_builds()
            self.assertEquals(1, self.s.query(m.Build).count())

    def test_refresh_latest_builds_skip_old(self):
        self.secondary_koji.getTaskInfo = Mock(return_value=rnv_task)
        self.secondary_koji.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages(['rnv'])[0]
        build = self.prepare_build('rnv', False)
        build.real = True
        build.epoch = None
        build.version = "1.7.11"
        build.release = "11.fc24"
        build.repo_id = 460889
        build.task_id = 12345678
        self.secondary_koji.listTagged = Mock(return_value=rnv_build_info)
        self.s.commit()
        with patch('koschei.backend.dispatch_event') as event:
            self.backend.refresh_latest_builds()
            self.assertEquals(1, self.s.query(m.Build).count())

    def test_cancel_timed_out(self):
        self.prepare_packages(['rnv'])
        running_build = self.prepare_build('rnv')
        running_build.started = datetime.datetime.now() - datetime.timedelta(999)
        self.s.commit()
        self.koji_session.cancelTask = Mock(side_effect=koji.GenericError)
        self.backend.update_build_state(running_build, 'FREE')
        self.koji_session.cancelTask.assert_called_once_with(running_build.task_id)
        self.assertEquals(0, self.s.query(m.Build).count())

    def test_cancel_requested(self):
        self.prepare_packages(['rnv'])
        running_build = self.prepare_build('rnv')
        running_build.cancel_requested = True
        self.s.commit()
        self.backend.update_build_state(running_build, 'ASSIGNED')
        self.koji_session.cancelTask.assert_called_once_with(running_build.task_id)
        self.assertEquals(0, self.s.query(m.Build).count())

    def test_set_group_contents(self):
        group = m.PackageGroup(name='foo')
        bar, a1, a2, a3 = self.prepare_packages(['bar', 'a1', 'a2', 'a3'])
        self.s.add(group)
        self.s.flush()
        rel = m.PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.s.add(rel)
        self.s.commit()
        content = ['a1', 'a2', 'a3']
        self.backend.set_group_content(group, content)

        self.assertItemsEqual([a1.base_id, a2.base_id, a3.base_id],
                              self.s.query(m.PackageGroupRelation.base_id)
                              .filter_by(group_id=group.id).all_flat())

    def test_append_group_content(self):
        group = m.PackageGroup(name='foo')
        self.s.add(group)
        self.s.flush()
        bar, a1, a2, a3 = self.prepare_packages(['bar', 'a1', 'a2', 'a3'])
        rel = m.PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.s.add(rel)
        self.s.commit()
        content = ['a1', 'a2', 'a3']
        self.backend.set_group_content(group, content, append=True)

        self.assertItemsEqual([bar.base_id, a1.base_id, a2.base_id, a3.base_id],
                              self.s.query(m.PackageGroupRelation.base_id)
                              .filter_by(group_id=group.id).all_flat())

    def test_refresh_packages(self):
        self.prepare_packages(['eclipse'])
        self.secondary_koji.listPackages.return_value = package_list
        self.backend.refresh_packages()
        eclipse = self.s.query(Package).filter_by(name='eclipse').one()
        rnv = self.s.query(Package).filter_by(name='eclipse').one()
        tools = self.s.query(Package).filter_by(name='maven-doxia-tools').one()
        self.assertFalse(eclipse.blocked)
        self.assertFalse(rnv.blocked)
        self.assertTrue(tools.blocked)
