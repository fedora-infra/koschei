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

import koji
import six

from copy import deepcopy
from datetime import datetime, timedelta

from test.common import DBTest
from test.koji_data import *
from mock import Mock, patch
from koschei import plugin, backend
from koschei.models import Package, Build, KojiTask

# pylint: disable=unbalanced-tuple-unpacking,blacklisted-name


class BackendTest(DBTest):
    def setUp(self):
        super(BackendTest, self).setUp()
        self.collection.secondary_mode = True
        self.db.commit()
        plugin.load_plugins('backend', ['fedmsg'])


    def test_update_state(self):
        self.session.koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
        running_build.task_id = rnv_task['id']
        self.db.commit()
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, running_build, 'CLOSED')
            self.session.koji_mock.getTaskInfo.assert_called_once_with(rnv_task['id'])
            self.session.koji_mock.getTaskChildren.assert_called_once_with(
                rnv_task['id'],
                request=True,
            )
            self.assertEqual('ok', package.state_string)
            event.assert_called_once_with('package_state_change',
                                          session=self.session,
                                          package=package,
                                          prev_state='failing', new_state='ok')
            six.assertCountEqual(self, [(x['id'],) for x in rnv_subtasks],
                                 self.db.query(KojiTask.task_id))

    def test_update_state_existing_task(self):
        self.session.koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
        running_build.task_id = rnv_task['id']
        koji_task = KojiTask(task_id=rnv_subtasks[0]['id'],
                             arch='noarch',
                             state=koji.TASK_STATES['OPEN'],
                             started=datetime.fromtimestamp(123),
                             build_id=running_build.id)
        self.db.add(koji_task)
        self.db.commit()
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, running_build, 'CLOSED')
            self.session.koji_mock.getTaskInfo.assert_called_once_with(rnv_task['id'])
            self.session.koji_mock.getTaskChildren.assert_called_once_with(
                rnv_task['id'],
                request=True,
            )
            self.assertEqual('ok', package.state_string)
            event.assert_called_once_with('package_state_change',
                                          session=self.session, package=package,
                                          prev_state='failing', new_state='ok')
            six.assertCountEqual(self, [(x['id'],) for x in rnv_subtasks],
                                 self.db.query(KojiTask.task_id))

    # Regression test for https://github.com/msimacek/koschei/issues/27
    def test_update_state_inconsistent(self):
        self.session.koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.koji_mock.getTaskChildren = Mock(return_value=inconsistent_subtask)
        package = self.prepare_packages('rnv')[0]
        self.prepare_build('rnv', False)
        running_build = self.prepare_build('rnv')
        running_build.task_id = rnv_task['id']
        self.db.commit()
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, running_build, 'CLOSED')
            self.session.koji_mock.getTaskInfo.assert_called_once_with(rnv_task['id'])
            self.session.koji_mock.getTaskChildren.assert_called_once_with(
                rnv_task['id'],
                request=True,
            )
            self.assertEqual('ok', package.state_string)
            event.assert_called_once_with('package_state_change', session=self.session,
                                          package=package,
                                          prev_state='failing', new_state='ok')

    def test_refresh_latest_builds(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.repo_id = 1
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.session.sec_koji_mock.getTaskInfo\
                .assert_called_once_with(rnv_build_info[0]['task_id'])
            self.session.sec_koji_mock.getTaskChildren\
                .assert_called_once_with(rnv_build_info[0]['task_id'],
                                         request=True)
            self.assertEqual('ok', package.state_string)
            self.assertEquals(460889, package.last_complete_build.repo_id)
            six.assertCountEqual(self, [(x['id'],) for x in rnv_subtasks],
                                 self.db.query(KojiTask.task_id))

    def test_refresh_latest_builds_already_present(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        self.prepare_packages('rnv')
        build = self.prepare_build('rnv', False)
        build.real = True
        build.repo_id = 460889
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        build.task_id = rnv_build_info[0]['task_id']
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.assertEqual(1, self.db.query(Build).count())

    def test_refresh_latest_builds_no_repo_id(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        subtasks = deepcopy(rnv_subtasks)
        for subtask in subtasks:
            del subtask['request']
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=subtasks)
        self.prepare_packages('rnv')
        build = self.prepare_build('rnv', False)
        build.real = True
        build.repo_id = 460889
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        build.task_id = 1234
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.assertEqual(1, self.db.query(Build).count())

    def test_refresh_latest_builds_skip_old(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        self.prepare_packages('rnv')
        build = self.prepare_build('rnv', False)
        build.real = True
        build.epoch = None
        build.version = "1.7.11"
        build.release = "11.fc24"
        build.repo_id = 460889
        build.task_id = 12345678
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.assertEqual(1, self.db.query(Build).count())

    def test_cancel_timed_out(self):
        self.prepare_packages('rnv')
        running_build = self.prepare_build('rnv')
        running_build.started = datetime.now() - timedelta(999)
        self.db.commit()
        self.session.koji_mock.cancelTask = Mock(side_effect=koji.GenericError)
        backend.update_build_state(self.session, running_build, 'FREE')
        self.session.koji_mock.cancelTask.assert_called_once_with(running_build.task_id)
        self.assertEqual(0, self.db.query(Build).count())

    def test_cancel_requested(self):
        self.prepare_packages('rnv')
        running_build = self.prepare_build('rnv')
        running_build.cancel_requested = True
        self.db.commit()
        backend.update_build_state(self.session, running_build, 'ASSIGNED')
        self.session.koji_mock.cancelTask.assert_called_once_with(running_build.task_id)
        self.assertEqual(0, self.db.query(Build).count())

    def test_refresh_packages(self):
        self.prepare_packages('eclipse')
        self.session.sec_koji_mock.listPackages.return_value = package_list
        backend.refresh_packages(self.session)
        eclipse = self.db.query(Package).filter_by(name='eclipse').one()
        rnv = self.db.query(Package).filter_by(name='eclipse').one()
        tools = self.db.query(Package).filter_by(name='maven-doxia-tools').one()
        self.assertFalse(eclipse.blocked)
        self.assertFalse(rnv.blocked)
        self.assertTrue(tools.blocked)

    def test_submit_build(self):
        package = self.prepare_packages('rnv')[0]
        with patch('koschei.backend.koji_util.get_last_srpm', return_value=({'epoch': '111',
                                                                             'version': '222',
                                                                             'release': '333'}, 'the_url')) as get_last_srpm:
            with patch('koschei.backend.koji_util.koji_scratch_build', return_value=7541) as koji_scratch_build:
                backend.submit_build(self.session, package)
                get_last_srpm.assert_called_once_with(self.session.sec_koji_mock, 'f25', 'rnv')
                koji_scratch_build.assert_called_once_with(self.session.koji_mock, 'f25', 'rnv', 'the_url', {})

    def test_submit_build_arch_override(self):
        package = self.prepare_packages('rnv')[0]
        package.arch_override = 'x86_64 alpha'
        self.db.commit()
        with patch('koschei.backend.koji_util.get_last_srpm', return_value=({'epoch': '111',
                                                                             'version': '222',
                                                                             'release': '333'}, 'the_url')) as get_last_srpm:
            with patch('koschei.backend.koji_util.koji_scratch_build', return_value=7541) as koji_scratch_build:
                backend.submit_build(self.session, package)
                get_last_srpm.assert_called_once_with(self.session.sec_koji_mock, 'f25', 'rnv')
                koji_scratch_build.assert_called_once_with(self.session.koji_mock, 'f25', 'rnv', 'the_url', {'arch_override': 'x86_64 alpha'})

    def test_submit_build_arch_exclude(self):
        package = self.prepare_packages('rnv')[0]
        package.arch_override = '^x86_64 alpha'
        self.db.commit()
        with patch('koschei.backend.koji_util.get_last_srpm', return_value=({'epoch': '111',
                                                                             'version': '222',
                                                                             'release': '333'}, 'the_url')) as get_last_srpm:
            with patch('koschei.backend.koji_util.koji_scratch_build', return_value=7541) as koji_scratch_build:
                backend.submit_build(self.session, package)
                get_last_srpm.assert_called_once_with(self.session.sec_koji_mock, 'f25', 'rnv')
                koji_scratch_build.assert_called_once_with(self.session.koji_mock, 'f25', 'rnv', 'the_url', {'arch_override': 'armhfp i386'})

    def test_submit_build_force_archful(self):
        package = self.prepare_packages('rnv')[0]
        package.arch_override = '^'
        self.db.commit()
        with patch('koschei.backend.koji_util.get_last_srpm', return_value=({'epoch': '111',
                                                                             'version': '222',
                                                                             'release': '333'}, 'the_url')) as get_last_srpm:
            with patch('koschei.backend.koji_util.koji_scratch_build', return_value=7541) as koji_scratch_build:
                backend.submit_build(self.session, package)
                get_last_srpm.assert_called_once_with(self.session.sec_koji_mock, 'f25', 'rnv')
                koji_scratch_build.assert_called_once_with(self.session.koji_mock, 'f25', 'rnv', 'the_url', {'arch_override': 'armhfp i386 x86_64'})
