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
    @with_koji_cassette
    def test_update_state_inconsistent(self):
        collection = self.prepare_collection('f29')
        package = self.prepare_package('rnv', collection=collection)
        self.prepare_build(package, 'complete')
        build = self.prepare_build(package, 'running', task_id=9107738)
        with patch('koschei.backend.dispatch_event') as event:
            backend.update_build_state(self.session, build, 'CLOSED')
            self.assertEqual('ok', package.state_string)
            self.assertFalse(event.called)

    @with_koji_cassette
    def test_refresh_latest_builds(self):
        self.db.delete(self.collection)
        collection = self.prepare_collection('f29')
        # rnv has a new real build, which changes state from failed to ok
        rnv = self.prepare_package('rnv', collection=collection)
        rnv_build = self.prepare_build(
            rnv, 'failed', version='1.7.11', release='14.fc28',
            task_id=25038558, started='2018-02-14 11:16:55',
        )
        # eclipse has a new build, no change in state
        eclipse = self.prepare_package('eclipse', collection=collection)
        eclipse_build = self.prepare_build(
            eclipse, 'complete', real=True, version='4.7.2', release='2.fc28',
            task_id=24736744, started='2018-02-05 20:42:27',
        )
        # maven has no new build
        maven = self.prepare_package('maven', collection=collection)
        maven_build = self.prepare_build(
            maven, 'complete', real=True, version='3.5.3', release='1.fc29',
            task_id=25725733, started='2018-03-15 14:13:38',
        )
        # slf4j has new build - was hand-edited to have no subtasks (-> no repo_id)
        slf4j = self.prepare_package('slf4j', collection=collection)
        slf4j_build = self.prepare_build(
            slf4j, 'complete', version='1.7.25', release='3.fc28',
            task_id=25743564, started='2018-03-16 14:15:22',
        )
        # lbzip2 has a latest build which is not in koji, should be untagged in koschei
        lbzip2 = self.prepare_package('lbzip2', collection=collection)
        lbzip2_build = self.prepare_build(
            lbzip2, 'complete', version='2.5', release='11.fc28',
            task_id=35743564, started='2018-02-20 14:35:33',
        )
        # log4j has a build marked as  untagged, but is tagged in koji - should
        # be retagged
        log4j = self.prepare_package('log4j', collection=collection)
        log4j_build = self.prepare_build(
            log4j, 'complete', real=True, version='2.9.1', release='2.fc28',
            untagged=True, task_id=22416320, started='2017-10-13 08:35:09',
        )

        with patch('koschei.backend.dispatch_event'):
            backend.refresh_latest_builds(self.session)
            self.db.commit()

        # ordinary real build
        self.assertEqual('ok', rnv.state_string)
        self.assertIsNot(rnv_build, rnv.last_build)
        self.assertIs(True, rnv.last_build.real)
        self.assertEqual(25162638, rnv.last_build.task_id)
        self.assertEqual(859626, rnv.last_build.repo_id)
        self.assertEqual(7, len(rnv.last_build.build_arch_tasks))

        # ordinary real build
        self.assertEqual('ok', eclipse.state_string)
        self.assertIsNot(eclipse_build, eclipse.last_build)
        self.assertIs(True, eclipse.last_build.real)
        self.assertEqual(25859902, eclipse.last_build.task_id)
        self.assertEqual(880568, eclipse.last_build.repo_id)
        self.assertEqual(7, len(eclipse.last_build.build_arch_tasks))

        # no new build
        self.assertIs(maven_build, maven.last_build)

        # latest build had no repo_id, was ignored
        self.assertIs(slf4j_build, slf4j.last_build)

        # should untag build
        self.assertIs(True, lbzip2_build.untagged)
        self.assertIsNot(lbzip2_build, lbzip2.last_build)
        self.assertEqual('10.fc28', lbzip2.last_build.release)

        # should retag build
        self.assertIs(False, log4j_build.untagged)
        self.assertIs(log4j_build, log4j.last_build)

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
