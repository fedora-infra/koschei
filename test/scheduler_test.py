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

from contextlib import contextmanager
from datetime import timedelta, datetime

from mock import Mock, patch
from sqlalchemy import Table, Column, Integer, MetaData

from test.common import DBTest
from koschei import models as m
from koschei.backend.services.scheduler import Scheduler


class SchedulerTest(DBTest):
    def get_scheduler(self):
        backend_mock = Mock()
        sched = Scheduler(db=self.s, koji_sessions={'primary': Mock(), 'secondary': Mock()},
                          backend=backend_mock)
        return sched

    def prepare_depchanges(self):
        build1 = self.prepare_build('rnv', True)
        build2 = self.prepare_build('rnv', True)
        build3 = self.prepare_build('eclipse', True)
        chngs = []
        # update, value 20
        chngs.append(m.UnappliedChange(package_id=build1.package_id, dep_name='expat',
                                       prev_version='2', curr_version='2',
                                       prev_release='rc1', curr_release='rc2',
                                       prev_build_id=build2.id,
                                       distance=1))
        # update - applied
        chngs.append(m.AppliedChange(dep_name='expat',
                                     prev_version='1', curr_version='2',
                                     prev_release='1', curr_release='rc1',
                                     distance=1, build_id=build2.id))
        # downgrade, value 10
        chngs.append(m.UnappliedChange(package_id=build1.package_id, dep_name='gcc',
                                       prev_version='11', curr_version='9',
                                       prev_release='19', curr_release='18',
                                       prev_build_id=build2.id,
                                       distance=2))
        # appearance, value 5
        chngs.append(m.UnappliedChange(package_id=build1.package_id, dep_name='python',
                                       prev_version=None, curr_version='3.3',
                                       prev_release=None, curr_release='11',
                                       prev_build_id=build2.id,
                                       distance=4))
        # null distance, value 2
        chngs.append(m.UnappliedChange(package_id=build1.package_id, dep_name='python-lxml',
                                       prev_version=None, curr_version='3.3',
                                       prev_release=None, curr_release='11',
                                       prev_build_id=build2.id,
                                       distance=None))
        # not from current build
        chngs.append(m.UnappliedChange(package_id=build1.package_id, dep_name='expat',
                                       prev_version='2', curr_version='2',
                                       prev_release='rc1', curr_release='rc2',
                                       prev_build_id=build1.id,
                                       distance=1))

        # different package - eclipse, value 20
        chngs.append(m.UnappliedChange(package_id=build3.package_id, dep_name='maven',
                                       prev_version='2', curr_version='2',
                                       prev_release='rc1', curr_release='rc2',
                                       prev_build_id=build3.id,
                                       distance=1))

        for chng in chngs:
            self.s.add(chng)
        self.s.commit()
        return build1.package, build3.package

    def assert_priority_query(self, query):
        columns = query.subquery().c
        self.assertIn('pkg_id', columns)
        self.assertIn('priority', columns)
        self.assertEqual(2, len(columns))

    def test_dependency_priority(self):
        rnv, eclipse = self.prepare_depchanges()
        query = self.get_scheduler().get_dependency_priority_query()
        self.assert_priority_query(query)
        res = query.all()
        self.assertIn((rnv.id, 20), res)
        self.assertIn((rnv.id, 10), res)
        self.assertIn((rnv.id, 5), res)
        self.assertIn((rnv.id, 2), res)
        self.assertIn((eclipse.id, 20), res)
        self.assertEqual(5, len(res))

    # regression test for #100
    def test_dependency_priority_unresolved_build_skipped(self):
        rnv, eclipse = self.prepare_depchanges()
        self.prepare_build('rnv', resolved=False)
        query = self.get_scheduler().get_dependency_priority_query()
        self.assert_priority_query(query)
        res = query.all()
        self.assertIn((rnv.id, 20), res)
        self.assertIn((rnv.id, 10), res)
        self.assertIn((rnv.id, 5), res)
        self.assertIn((rnv.id, 2), res)
        self.assertIn((eclipse.id, 20), res)
        self.assertEqual(5, len(res))

    def test_time_priority(self):
        for days in [0, 2, 5, 7, 12]:
            pkg = m.Package(name='p{}'.format(days), collection_id=self.collection.id)
            self.ensure_base_package(pkg)
            self.s.add(pkg)
            self.s.flush()
            build = m.Build(package_id=pkg.id,
                            started=datetime.now() - timedelta(days, hours=1),
                            version='1', release='1.fc25',
                            task_id=days + 1)
            self.s.add(build)
        self.s.commit()
        query = self.get_scheduler().get_time_priority_query()
        self.assert_priority_query(query)
        res = sorted(query.all(), key=lambda x: x.priority)
        self.assertEqual(5, len(res))
        expected_prios = [-30.0, 161.339324401, 230.787748579,
                          256.455946637, 297.675251883]
        for item, exp in zip(res, expected_prios):
            self.assertAlmostEqual(exp, item.priority, places=-1)

    def test_failed_build_priority(self):
        pkgs = self.prepare_packages(['rnv', 'eclipse', 'fop', 'freemind', 'i3',
                                      'maven', 'firefox'])
        self.prepare_build('rnv', True)
        self.prepare_build('eclipse', False)
        self.prepare_build('i3', True)
        self.prepare_build('freemind', False)
        self.prepare_build('maven', True)
        self.prepare_build('firefox', True)
        self.prepare_build('rnv', False)
        self.prepare_build('eclipse', False)
        self.prepare_build('fop', False)
        self.prepare_build('freemind', False)
        self.prepare_build('maven', False)
        self.prepare_build('maven_resolved', False)
        self.prepare_build('freemind', False)
        self.prepare_build('firefox', True, resolved=False)
        self.prepare_build('maven', False, resolved=False)
        query = self.get_scheduler().get_failed_build_priority_query()
        # fop has 1 failed build with no previous one, should it be prioritized?
        # self.assertItemsEqual([(pkgs[0].id, 200), (pkgs[1].id, 200)],
        #                       query.all())

        # schedules rnv and firefox
        self.assertItemsEqual([(pkgs[0].id, 200), (pkgs[6].id, 200)], query.all())

    def test_coefficient(self):
        rnv, eclipse, fop = self.prepare_packages(['rnv', 'eclipse', 'fop'])
        eclipse_coll = m.Collection(name='eclipse', display_name='eclipse',
                                    build_tag='foo', target_tag='foo',
                                    priority_coefficient=0.1)
        self.s.add(eclipse_coll)
        self.s.flush()
        eclipse.collection_id = eclipse_coll.id
        self.prepare_build('rnv', True)
        self.prepare_build('rnv', False)
        self.prepare_build('eclipse', True)
        self.prepare_build('eclipse', False)
        eclipse.manual_priority = 500
        priorities = self.get_scheduler().get_priorities()
        self.assertEquals(eclipse.id, priorities[0][0])
        self.assertAlmostEqual(517, priorities[0][1], places=1)
        self.assertEquals(rnv.id, priorities[1][0])
        self.assertAlmostEqual(170, priorities[1][1], places=1)
        self.assertEquals(fop.id, priorities[2][0])
        self.assertAlmostEqual(0, priorities[2][1], places=1)
        self.assertEqual(3, len(priorities))

    @contextmanager
    def prio_table(self, tablename='tmp', **kwargs):
        try:
            table = Table(tablename, MetaData(),
                          Column('pkg_id', Integer), Column('priority', Integer))
            conn = self.s.connection()
            table.create(bind=conn)
            priorities = {name: prio for name, prio in kwargs.items() if '_' not in name}
            builds = {name[:-len("_build")]: state for name, state in kwargs.items()
                      if name.endswith('_build')}
            states = {name[:-len('_state')]: state for name, state in kwargs.items()
                      if name.endswith('_state')}
            pkgs = []
            for name in priorities.keys():
                pkg = self.s.query(m.Package).filter_by(name=name).first()
                if not pkg:
                    pkg = m.Package(name=name, tracked=states.get(name) != 'ignored',
                                    collection_id=self.collection.id)
                    self.ensure_base_package(pkg)
                    self.s.add(pkg)
                    self.s.flush()
                    if states.get(name, True) is not None:
                        pkg.resolved = states.get(name) != 'unresolved'
                pkgs.append((name, pkg))
                if name in builds:
                    self.s.add(m.Build(package_id=pkg.id, state=builds[name],
                                       task_id=self.task_id_counter,
                                       version='1', release='1.fc25',
                                       repo_id=1 if builds[name] != m.Build.RUNNING else None))
                    self.task_id_counter += 1
            conn.execute(table.insert(), [{'pkg_id': pkg.id, 'priority': priorities[name]}
                                          for name, pkg in pkgs])
            self.s.commit()
            yield table
        finally:
            self.s.rollback()
            conn = self.s.connection()
            table.drop(bind=conn, checkfirst=True)
            self.s.commit()

    def assert_scheduled(self, tables, scheduled, koji_load=0.3):
        with patch('koschei.backend.koji_util.get_koji_load',
                   Mock(return_value=koji_load)):
            sched = self.get_scheduler()
            def get_prio_q():
                return {i :self.s.query(t.c.pkg_id.label('pkg_id'), t.c.priority.label('priority'))
                        for i, t in enumerate(tables)}
            with patch.object(sched, 'get_priority_queries', get_prio_q):
                sched.main()
                if scheduled:
                    pkg = self.s.query(m.Package).filter_by(name=scheduled).one()
                    sched.backend.submit_build.assert_called_once_with(pkg)
                else:
                    self.assertFalse(sched.backend.submit_build.called)

    def test_low(self):
        with self.prio_table(rnv=10) as table:
            self.assert_scheduled([table], scheduled=None)

    def test_submit1(self):
        with self.prio_table(rnv=256) as table:
            self.assert_scheduled([table], scheduled='rnv')

    def test_submit_no_resolution(self):
        with self.prio_table(rnv=256, rnv_state=None) as table:
            self.assert_scheduled([table], scheduled='rnv')

    def test_load(self):
        with self.prio_table(rnv=30000) as table:
            self.assert_scheduled([table], koji_load=0.7, scheduled=None)

    def test_max_builds(self):
        with self.prio_table(rnv=30, rnv_build=m.Build.RUNNING,
                             eclipse=300, eclipse_build=m.Build.RUNNING,
                             expat=400) as table:
            self.assert_scheduled([table], scheduled=None)

    def test_running1(self):
        with self.prio_table(rnv=30000, rnv_build=m.Build.RUNNING) as table:
            self.assert_scheduled([table], scheduled=None)

    def test_running2(self):
        with self.prio_table(eclipse=100, rnv=300, rnv_build=m.Build.RUNNING) as table:
            self.assert_scheduled([table], scheduled=None)

    def test_running3(self):
        with self.prio_table(eclipse=280, rnv=300, rnv_build=m.Build.RUNNING) as table:
            self.assert_scheduled([table], scheduled='eclipse')

    def test_multiple(self):
        with self.prio_table(eclipse=280, rnv=300) as table:
            self.assert_scheduled([table], scheduled='rnv')

    def test_builds(self):
        with self.prio_table(eclipse=100, rnv=300, rnv_build=m.Build.COMPLETE,
                             eclipse_build=m.Build.RUNNING) as table:
            self.assert_scheduled([table], scheduled='rnv')

    def test_state1(self):
        with self.prio_table(rnv=300, rnv_state='unresolved') as table:
            self.assert_scheduled([table], scheduled=None)

    def test_state2(self):
        with self.prio_table(rnv=300, rnv_state='ignored') as table:
            self.assert_scheduled([table], scheduled=None)

    def test_union1(self):
        with self.prio_table(tablename='tmp1', rnv=100) as table1:
            with self.prio_table(tablename='tmp2', eclipse=280, rnv=200) as table2:
                self.assert_scheduled([table1, table2], scheduled='rnv')

    def test_union2(self):
        with self.prio_table(tablename='tmp1', rnv=100) as table1:
            with self.prio_table(tablename='tmp2', eclipse=300, rnv=200) as table2:
                with self.prio_table(tablename='tmp3', eclipse=201, rnv=200) as table3:
                    self.assert_scheduled([table1, table2, table3], scheduled='eclipse')
