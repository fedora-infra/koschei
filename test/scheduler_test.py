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
from sqlalchemy import literal_column
from datetime import datetime

from test.common import DBTest, with_koji_cassette
from koschei.models import Build, Package
from koschei.backend.services.scheduler import Scheduler


# pylint:disable = too-many-public-methods, unbalanced-tuple-unpacking
class SchedulerTest(DBTest):
    def get_scheduler(self):
        sched = Scheduler(self.session)
        return sched

    def prepare_priorities(self, **kwargs):
        priorities = {
            name: prio for name, prio in list(kwargs.items()) if '_' not in name
        }
        builds = {name[:-len("_build")]: state for name, state in list(kwargs.items())
                  if name.endswith('_build')}
        states = {name[:-len('_state')]: state for name, state in list(kwargs.items())
                  if name.endswith('_state')}
        pkgs = []
        for name in list(priorities.keys()):
            pkg = self.prepare_package(
                name=name,
                tracked=states.get(name) != 'ignored',
                # add 30 to offset time priority which will be -30
                dependency_priority=priorities[name] + 30,
            )
            self.prepare_build(
                package=pkg,
                state=Build.COMPLETE,
                task_id=self.task_id_counter,
                version='1',
                release='1.fc25',
                started=datetime(2017, 10, 10, 10, self.task_id_counter),
                repo_id=1,
            )
            self.task_id_counter += 1
            if states.get(name, True) is not None:
                pkg.resolved = states.get(name) != 'unresolved'
            pkgs.append((name, pkg))
            if name in builds:
                self.prepare_build(
                    package=pkg,
                    state=builds[name],
                    task_id=self.task_id_counter,
                    version='1',
                    release='1.fc25',
                    started=datetime(2017, 10, 10, 10, self.task_id_counter),
                    repo_id=1 if builds[name] != Build.RUNNING else None,
                )
                self.task_id_counter += 1
        self.db.commit()

    def assert_scheduled(self, scheduled, koji_load=0.3):
        with patch('koschei.backend.koji_util.get_koji_load',
                   Mock(return_value=koji_load)), \
             patch('koschei.backend.koji_util.get_srpm_arches',
                   Mock(return_value=['x86_64'])), \
             patch('koschei.backend.koji_util.get_koji_arches_cached',
                   Mock(return_value=['x86_64'])):
            sched = self.get_scheduler()
            with patch('sqlalchemy.sql.expression.func.clock_timestamp',
                       return_value=literal_column("'2017-10-10 10:50:00'")):
                with patch('koschei.backend.submit_build') as submit_mock:
                    sched.main()
                    if scheduled:
                        pkg = self.db.query(Package).filter_by(name=scheduled).one()
                        submit_mock.assert_called_once_with(self.session, pkg,
                                                            arch_override=['x86_64'])
                    else:
                        self.assertFalse(submit_mock.called)

    def test_low(self):
        self.prepare_priorities(rnv=10)
        self.assert_scheduled(None)

    def test_submit1(self):
        self.prepare_priorities(rnv=256)
        self.assert_scheduled('rnv')

    def test_submit_no_resolution(self):
        self.prepare_priorities(rnv=256, rnv_state=None)
        self.assert_scheduled(None)

    def test_load(self):
        self.prepare_priorities(rnv=30000)
        self.assert_scheduled(None, koji_load=0.7)

    def test_max_builds(self):
        self.prepare_priorities(rnv=30, rnv_build=Build.RUNNING,
                                eclipse=300, eclipse_build=Build.RUNNING,
                                expat=400)
        self.assert_scheduled(None)

    def test_running1(self):
        self.prepare_priorities(rnv=30000, rnv_build=Build.RUNNING)
        self.assert_scheduled(None)

    def test_running2(self):
        self.prepare_priorities(eclipse=100, rnv=300, rnv_build=Build.RUNNING)
        self.assert_scheduled(None)

    def test_running3(self):
        self.prepare_priorities(eclipse=280, rnv=300, rnv_build=Build.RUNNING)
        self.assert_scheduled('eclipse')

    def test_multiple(self):
        self.prepare_priorities(eclipse=280, rnv=300)
        self.assert_scheduled('rnv')

    def test_builds(self):
        self.prepare_priorities(eclipse=100, rnv=300, rnv_build=Build.COMPLETE,
                                eclipse_build=Build.RUNNING)
        self.assert_scheduled('rnv')

    def test_state1(self):
        self.prepare_priorities(rnv=300, rnv_state='unresolved')
        self.assert_scheduled(None)

    def test_state2(self):
        self.prepare_priorities(rnv=300, rnv_state='ignored')
        self.assert_scheduled(None)

    def test_broken_buildroot(self):
        self.collection.latest_repo_resolved = False
        self.prepare_priorities(rnv=256, rnv_state=None)
        self.assert_scheduled(None)

    def test_buildroot_not_yet_resolved(self):
        self.collection.latest_repo_resolved = None
        self.collection.latest_repo_id = None
        self.prepare_priorities(rnv=256, rnv_state=None)
        self.assert_scheduled(None)

    def test_load_not_determined_when_no_schedulable_packages(self):
        with patch('koschei.backend.koji_util.get_koji_load') as load_mock, \
                patch('koschei.backend.koji_util.get_srpm_arches',
                      Mock(return_value=['x86_64'])):
            self.get_scheduler().main()
            load_mock.assert_not_called()

    def test_skipped_resolution(self):
        self.prepare_priorities(rnv=256, rnv_state=None)
        self.db.query(Package).filter_by(name='rnv').first().skip_resolution = True
        self.db.commit()
        self.assert_scheduled('rnv')

    @with_koji_cassette
    def test_submit_integration(self):
        """Test submission without mocking koji_util"""
        collection = self.prepare_collection('f29')
        maven = self.prepare_package(
            name='maven',
            collection=collection,
            manual_priority=10000,
            resolved=True,
        )
        build = self.prepare_build(
            package=maven,
            state='complete',
            version='3.5.3',
            release='2.fc29',
            started=datetime.now(),
        )
        scheduler = Scheduler(self.session)
        scheduler.main()
        new_build = maven.last_build
        self.assertIsNot(build, new_build)
        self.assertEqual(Build.RUNNING, new_build.state)
        self.assertEqual(27278005, new_build.task_id)
        self.assertEqual('3.5.3', new_build.version)
        self.assertEqual('2.fc29', new_build.release)
