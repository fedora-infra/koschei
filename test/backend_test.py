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

from copy import deepcopy
from datetime import datetime, timedelta

from test.common import DBTest, with_koji_cassette
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

    @with_koji_cassette('BackendTest/test_update_state')
    def test_update_state(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        self.prepare_build(package, 'failed')
        build = self.prepare_build(package, 'running', task_id=9107738)
        self.assertEqual('failing', package.state_string)
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, build, 'CLOSED')
            self.assertEqual('complete', build.state_string)
            self.assertEqual('ok', package.state_string)
            self.assertEqual(0, package.build_priority)
            event.assert_called_once_with(
                'package_state_change',
                session=self.session,
                package=package,
                prev_state='failing',
                new_state='ok',
            )
            # ordered by arch
            tasks = build.build_arch_tasks
            self.assertEqual(3, len(tasks))

            self.assertEqual(9107739, tasks[0].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[0].state)
            self.assertEqual('armhfp', tasks[0].arch)

            self.assertEqual(9107741, tasks[1].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[1].state)
            self.assertEqual('i386', tasks[1].arch)

            self.assertEqual(9107740, tasks[2].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[2].state)
            self.assertEqual('x86_64', tasks[2].arch)

    @with_koji_cassette('BackendTest/test_update_state_failed')
    def test_update_state_failed(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('eclipse', collection=collection)
        self.prepare_build(package, 'complete')
        build = self.prepare_build(package, 'running', task_id=14503213)
        self.db.commit()
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, build, 'FAILED')
            self.assertEqual('failed', build.state_string)
            self.assertEqual('failing', package.state_string)
            self.assertEqual(200, package.build_priority)
            event.assert_called_once_with(
                'package_state_change',
                session=self.session,
                package=package,
                prev_state='ok',
                new_state='failing',
            )

    @with_koji_cassette('BackendTest/test_update_state_failed')
    def test_update_state_already_failing(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('eclipse', collection=collection)
        self.prepare_build(package, 'failed')
        build = self.prepare_build(package, 'running', task_id=14503213)
        self.db.commit()
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, build, 'FAILED')
            self.assertEqual('failed', build.state_string)
            self.assertEqual('failing', package.state_string)
            self.assertEqual(0, package.build_priority)
            self.assertFalse(event.called)

    @with_koji_cassette('BackendTest/test_update_state')
    def test_update_state_existing_task(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        self.prepare_build(package, 'failed')
        build = self.prepare_build(package, 'running', task_id=9107738)
        koji_task = KojiTask(
            task_id=9107739,
            arch='armhfp',
            state=koji.TASK_STATES['OPEN'],
            started=datetime.fromtimestamp(123),
            build=build,
        )
        self.db.add(koji_task)
        self.db.commit()
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, build, 'CLOSED')
            self.assertEqual('complete', build.state_string)
            self.assertEqual('ok', package.state_string)
            self.assertEqual(0, package.build_priority)
            event.assert_called_once_with(
                'package_state_change',
                session=self.session,
                package=package,
                prev_state='failing',
                new_state='ok',
            )
            # ordered by arch
            tasks = build.build_arch_tasks
            self.assertEqual(3, len(tasks))

            self.assertEqual(9107739, tasks[0].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[0].state)
            self.assertEqual('armhfp', tasks[0].arch)

            self.assertEqual(9107741, tasks[1].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[1].state)
            self.assertEqual('i386', tasks[1].arch)

            self.assertEqual(9107740, tasks[2].task_id)
            self.assertEqual(koji.TASK_STATES['CLOSED'], tasks[2].state)
            self.assertEqual('x86_64', tasks[2].arch)

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
            self.assertEqual(460889, package.last_complete_build.repo_id)
            self.assertCountEqual([(x['id'],) for x in rnv_subtasks],
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

    def test_refresh_latest_builds_rewind_untagged(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        rnv = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.real = True
        build.epoch = None
        build.version = "1.7.11"
        build.release = "11.fc24"
        build.repo_id = 460889
        build.task_id = 12345678
        build.started = '2017-02-05 04:34:41'
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        self.assertTrue(build.last_complete)
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.db.commit()
            self.assertEqual(2, self.db.query(Build).count())
            self.assertEqual("10.fc24", rnv.last_complete_build.release)
            self.assertIs(rnv.last_complete_build, rnv.last_build)
            self.assertTrue(rnv.last_complete_build.last_complete)
            self.assertFalse(build.last_complete)

    def test_refresh_latest_builds_retag(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        rnv = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.real = True
        build.repo_id = 460889
        build.epoch = rnv_build_info[0]['epoch']
        build.version = rnv_build_info[0]['version']
        build.release = rnv_build_info[0]['release']
        build.task_id = rnv_build_info[0]['task_id']
        build.untagged = True
        self.session.sec_koji_mock.listTagged = Mock(return_value=rnv_build_info)
        self.db.commit()
        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.assertEqual(1, self.db.query(Build).count())
            self.assertEqual(build, rnv.last_build)

    def test_register_real_builds(self):
        self.session.sec_koji_mock.getTaskInfo = Mock(return_value=rnv_task)
        self.session.sec_koji_mock.getTaskChildren = Mock(return_value=rnv_subtasks)
        package = self.prepare_packages('rnv')[0]
        build = self.prepare_build('rnv', False)
        build.repo_id = 1
        build.epoch = None
        build.version = "1.7.11"
        build.release = "9.fc24"
        self.db.commit()
        build_infos = [(package.id, rnv_build_info[0])]
        backend.register_real_builds(self.session, self.collection, build_infos)
        self.assertEqual(2, self.db.query(Build).count())
        # now test that it won't insert duplicates
        backend.register_real_builds(self.session, self.collection, build_infos)
        self.assertEqual(2, self.db.query(Build).count())

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

    @with_koji_cassette
    def test_refresh_packages(self):
        eclipse = self.prepare_package('eclipse')
        rnv = self.prepare_package('rnv', blocked=False)
        backend.refresh_packages(self.session)
        tools = self.db.query(Package).filter_by(name='maven-doxia-tools').one()
        self.assertFalse(eclipse.blocked)
        self.assertFalse(rnv.blocked)
        self.assertTrue(tools.blocked)
        self.assertEqual(9, self.db.query(Package).count())

    @with_koji_cassette
    def test_submit_build(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        backend.submit_build(self.session, package)
        self.db.commit()
        self.assertIsNot(None, package.last_build)
        self.assertEqual(Build.RUNNING, package.last_build.state)
        self.assertEqual(25560834, package.last_build.task_id)
        self.assertIsNone(package.last_build.repo_id)
        self.assertEqual(None, package.last_build.epoch)
        self.assertEqual('1.7.11', package.last_build.version)
        self.assertEqual('15.fc28', package.last_build.release)
        self.assertIsNot(None, package.last_build.started)

    @with_koji_cassette
    def test_submit_build_arch_override(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        arch_override = ['x86_64', 'armhfp']
        backend.submit_build(self.session, package, arch_override=arch_override)
        self.db.commit()
        self.assertEqual(25561246, package.last_build.task_id)

    @with_koji_cassette
    def test_submit_build_from_repo_id(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        self.session.build_from_repo_id_override = True
        backend.submit_build(self.session, package)
        self.db.commit()
        self.assertEqual(25560834, package.last_build.task_id)
        self.assertEqual(123, package.last_build.repo_id)
