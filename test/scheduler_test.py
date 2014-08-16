import koji

from datetime import datetime, timedelta
from mock import Mock, patch
from common import AbstractTest, MockDatetime

from koschei import models as m, scheduler

scheduler.datetime = MockDatetime

class SchedulerTest(AbstractTest):
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

        # 0 distance
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
        query = scheduler.get_dependency_priority_query(self.s)
        self.assert_priority_query(query)
        res = query.all()
        self.assertEqual(3, len(res))
        self.assertIn((pkg.id, 20), res)
        self.assertIn((pkg.id, 10), res)
        self.assertIn((pkg.id, 5), res)

    def test_time_priority(self):
        for days in [0, 2, 5, 7, 12]:
            pkg = m.Package(name='p{}'.format(days))
            self.s.add(pkg)
            self.s.flush()
            build = m.Build(package_id=pkg.id,
                          started=MockDatetime.now() - timedelta(days, hours=1))
            self.s.add(build)
        self.s.commit()
        query = scheduler.get_time_priority_query(self.s)
        self.assert_priority_query(query)
        res = sorted(query.all(), key=lambda x: x.priority)
        self.assertEqual(5, len(res))
        expected_prios = [-30.0, 161.339324401, 230.787748579,
                          256.455946637, 297.675251883]
        for item, exp in zip(res, expected_prios):
            self.assertAlmostEqual(exp, item.priority)
