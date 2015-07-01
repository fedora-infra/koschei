from datetime import timedelta
from contextlib import contextmanager
from common import DBTest, MockDatetime, postgres_only
from sqlalchemy import Table, Column, Integer, MetaData
from mock import Mock, patch

from koschei import models as m, scheduler
from koschei.scheduler import Scheduler

scheduler.datetime = MockDatetime

class SchedulerTest(DBTest):
    def get_scheduler(self):
        sched = Scheduler(db=self.s, koji_session=Mock(), backend=Mock())
        sched.lock_package_table = lambda: None
        return sched

    def prepare_depchanges(self):
        pkg, build = self.prepare_basic_data()
        chngs = []
        # update, value 20
        chngs.append(m.DependencyChange(package_id=pkg.id, dep_name='expat',
                                        prev_version='2', curr_version='2',
                                        prev_release='rc1', curr_release='rc2',
                                        distance=1))
        # update - applied
        chngs.append(m.DependencyChange(package_id=pkg.id, dep_name='expat',
                                        prev_version='1', curr_version='2',
                                        prev_release='1', curr_release='rc1',
                                        distance=1, applied_in_id=build.id))
        # downgrade, value 10
        chngs.append(m.DependencyChange(package_id=pkg.id, dep_name='gcc',
                                        prev_version='11', curr_version='9',
                                        prev_release='19', curr_release='18',
                                        distance=2))
        # appearance, value 5
        chngs.append(m.DependencyChange(package_id=pkg.id, dep_name='python',
                                        prev_version=None, curr_version='3.3',
                                        prev_release=None, curr_release='11',
                                        distance=4))

        # null distance, value 2
        chngs.append(m.DependencyChange(package_id=pkg.id, dep_name='python-lxml',
                                        prev_version=None, curr_version='3.3',
                                        prev_release=None, curr_release='11',
                                        distance=None))

        for chng in chngs:
            self.s.add(chng)
        self.s.commit()
        return pkg, build

    def assert_priority_query(self, query):
        columns = query.subquery().c
        self.assertIn('pkg_id', columns)
        self.assertIn('priority', columns)
        self.assertEqual(2, len(columns))

    def test_dependency_priority(self):
        pkg, _ = self.prepare_depchanges()
        query = self.get_scheduler().get_dependency_priority_query()
        self.assert_priority_query(query)
        res = query.all()
        self.assertIn((pkg.id, 20), res)
        self.assertIn((pkg.id, 10), res)
        self.assertIn((pkg.id, 5), res)
        self.assertIn((pkg.id, 2), res)
        self.assertEqual(4, len(res))

    @postgres_only
    def test_time_priority(self):
        for days in [0, 2, 5, 7, 12]:
            pkg = m.Package(name='p{}'.format(days))
            self.s.add(pkg)
            self.s.flush()
            build = m.Build(package_id=pkg.id,
                            started=MockDatetime.now() - timedelta(days, hours=1))
            self.s.add(build)
        self.s.commit()
        query = self.get_scheduler().get_time_priority_query()
        self.assert_priority_query(query)
        res = sorted(query.all(), key=lambda x: x.priority)
        self.assertEqual(5, len(res))
        expected_prios = [-30.0, 161.339324401, 230.787748579,
                          256.455946637, 297.675251883]
        for item, exp in zip(res, expected_prios):
            self.assertAlmostEqual(exp, item.priority)

    @postgres_only
    def test_failed_build_priority(self):
        pkgs = self.prepare_packages(['rnv', 'eclipse', 'fop', 'freemind', 'i3'])
        self.prepare_builds(rnv=True, eclipse=False, i3=True, freemind=False)
        self.prepare_builds(rnv=False, eclipse=False, fop=False, freemind=False)
        self.prepare_builds(freemind=False)
        query = self.get_scheduler().get_failed_build_priority_query()
        # fop has 1 failed build with no previous one, should it be prioritized?
        # self.assertItemsEqual([(pkgs[0].id, 200), (pkgs[1].id, 200)],
        #                       query.all())
        self.assertItemsEqual([(pkgs[0].id, 200)], query.all())

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
                    pkg = m.Package(name=name, ignored=states.get(name) == 'ignored')
                    self.s.add(pkg)
                    self.s.flush()
                    if states.get(name, True) is not None:
                        pkg.resolved = states.get(name) != 'unresolved'
                pkgs.append((name, pkg))
                if name in builds:
                    self.s.add(m.Build(package_id=pkg.id, state=builds[name]))
            conn.execute(table.insert(), [{'pkg_id': pkg.id, 'priority': priorities[name]}
                                          for name, pkg in pkgs])
            self.s.commit()
            yield table
        finally:
            self.s.rollback()
            conn = self.s.connection()
            table.drop(bind=conn)
            self.s.commit()

    def assert_scheduled(self, tables, scheduled, koji_load=0.3):
        with patch('koschei.util.get_koji_load',
                   Mock(return_value=koji_load)):
            sched = self.get_scheduler()
            def get_prio_q():
                return {i :self.s.query(t.c.pkg_id.label('pkg_id'), t.c.priority.label('priority'))
                        for i, t in enumerate(tables)}
            with patch.object(sched, 'get_priority_queries', get_prio_q):
                sched.main()
                if scheduled:
                    pkg = self.s.query(m.Package).filter_by(name=scheduled).one()
                    sched.backend.submit_build.assertCalledOnceWith(pkg)
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
