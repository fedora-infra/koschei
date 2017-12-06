# Copyright (C) 2016 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from mock import patch
from sqlalchemy import literal_column
from datetime import datetime, timedelta

from koschei.models import (
    Package, Collection, Build, ResourceConsumptionStats, ScalarStats, KojiTask,
    PackageGroup,
)
from test.common import DBTest


class GroupTest(DBTest):
    def test_group_name_format(self):
        group1 = self.prepare_group('foo', content=['foo'])
        group2 = self.prepare_group('bar', namespace='ns', content=['foo'])
        self.assertEqual('foo', group1.full_name)
        self.assertEqual('ns/bar', group2.full_name)

    def test_group_name_parse(self):
        self.assertEqual((None, 'foo'), PackageGroup.parse_name('foo'))
        self.assertEqual(('ns', 'bar'), PackageGroup.parse_name('ns/bar'))

    def test_group_cardinality(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        self.assertEqual(3, group.package_count)

    def test_group_cardinality_multiple_groups(self):
        group1 = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        group2 = self.prepare_group('dsfla', content=['abc', 'def', 'ghi', 'jkl'])
        self.assertEqual(3, group1.package_count)
        self.assertEqual(4, group2.package_count)

    def test_group_cardinality_multiple_collections(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        new_collection = Collection(name="new", display_name="New",
                                    target='foo', build_tag="build_tag2",
                                    priority_coefficient=2.0)
        self.db.add(new_collection)
        self.db.commit()
        pkg = Package(name='bar', collection_id=new_collection.id)
        self.ensure_base_package(pkg)
        self.db.add(pkg)
        self.db.commit()
        self.assertEqual(3, group.package_count)

    def test_group_cardinality_blocked(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        self.prepare_packages('bar')[0].blocked = True
        self.db.commit()
        self.assertEqual(2, group.package_count)

    def test_group_cardinality_partially_blocked(self):
        # Package xalan-j2 is blocked in one collection only.
        group = self.prepare_group('xyzzy', content=['xalan-j2'])
        self.prepare_packages('xalan-j2')[0].blocked = True
        self.db.commit()
        new_collection = Collection(name="new", display_name="New",
                                    target='foo', build_tag="build_tag2",
                                    priority_coefficient=2.0)
        self.db.add(new_collection)
        self.db.commit()
        pkg = Package(name='xalan-j2', collection_id=new_collection.id)
        self.ensure_base_package(pkg)
        self.db.add(pkg)
        self.db.commit()
        self.assertEqual(1, group.package_count)

    def test_group_cardinality_fully_blocked(self):
        # Package xalan-j2 is blocked in all collections.
        group = self.prepare_group('xyzzy', content=['xalan-j2'])
        self.prepare_packages('xalan-j2')[0].blocked = True
        self.db.commit()
        new_collection = Collection(name="new", display_name="New",
                                    target='foo', build_tag="build_tag2",
                                    priority_coefficient=2.0)
        self.db.add(new_collection)
        self.db.commit()
        pkg = Package(name='xalan-j2', collection_id=new_collection.id, blocked=True)
        self.ensure_base_package(pkg)
        self.db.add(pkg)
        self.db.commit()
        self.assertEqual(0, group.package_count)


class PackageStateStringTest(DBTest):
    def verify_state_string(self, state_string, **pkg_kwargs):
        pkg = self.prepare_package(**pkg_kwargs)
        self.assertEqual(state_string, pkg.state_string)
        self.assertEqual(
            state_string,
            self.db.query(Package.state_string)
            .filter(Package.id == pkg.id)
            .scalar()
        )

    def test_state_string(self):
        self.verify_state_string('blocked', blocked=True)
        self.verify_state_string('untracked', tracked=False)
        self.verify_state_string('unresolved', resolved=False)
        self.verify_state_string('ok', resolved=True,
                                 last_complete_build_state=Build.COMPLETE)
        self.verify_state_string('failing', resolved=True,
                                 last_complete_build_state=Build.FAILED)
        self.verify_state_string('unknown', resolved=None,
                                 last_complete_build_state=None)
        self.verify_state_string('unknown', resolved=True,
                                 last_complete_build_state=None)


@patch('sqlalchemy.sql.expression.func.clock_timestamp',
       return_value=literal_column("'2017-10-10 10:00:00'"))
class PackagePriorityTest(DBTest):
    def setUp(self):
        super(PackagePriorityTest, self).setUp()
        self.pkg = self.prepare_packages('rnv')[0]
        self.pkg.resolved = True
        self.build = self.prepare_build('rnv', state=True)
        self.build.started = '2017-10-10 10:00:00'

    def get_priority(self, pkg):
        return self.db.query(
            Package.current_priority_expression(
                collection=pkg.collection,
                last_build=pkg.last_build,
            )
        ).filter(Package.id == pkg.id).scalar()

    def get_priority_join(self, pkg):
        return self.db.query(
            Package.current_priority_expression(
                collection=Collection,
                last_build=Build,
            )
        ).join(Package.collection)\
            .join(Package.last_build)\
            .filter(Package.id == pkg.id).scalar()

    def verify_priority(self, expected, pkg=None):
        pkg = pkg or self.pkg
        self.db.commit()
        priority = self.get_priority(pkg)
        priority_join = self.get_priority_join(pkg)
        if expected:
            self.assertIsNotNone(priority)
            self.assertIsNotNone(priority_join)
            self.assertAlmostEqual(expected, priority)
            self.assertAlmostEqual(expected, priority_join)
        else:
            self.assertIsNone(priority)
            self.assertIsNone(priority_join)

    def test_basic(self, _):
        # time priority for just completed build, no other values
        self.verify_priority(-30)

    def test_coefficient(self, _):
        self.pkg.manual_priority = 10
        self.pkg.static_priority = 20
        self.pkg.dependency_priority = 40
        self.pkg.build_priority = 50
        self.pkg.collection.priority_coefficient = 0.5
        self.verify_priority(10 + 20 + 0.5 * (-30 + 40 + 50))

    def test_time(self, _):
        # 2 h difference
        self.build.started = '2017-10-10 08:00:00'
        self.verify_priority(-30)
        # 10 h difference
        self.build.started = '2017-10-10 00:00:00'
        self.verify_priority(39.2446980024098)
        # 1 day difference
        self.build.started = '2017-10-9 00:00:00'
        self.verify_priority(133.26248998925)
        # 1 month difference
        self.build.started = '2017-9-10 00:00:00'
        self.verify_priority(368.863607520133)

    def test_untracked(self, _):
        self.pkg.tracked = False
        self.verify_priority(None)

    def test_blocked(self, _):
        self.pkg.blocked = True
        self.verify_priority(None)

    def test_unresolved(self, _):
        self.pkg.resolved = False
        self.verify_priority(None)

    def test_running_build(self, _):
        self.prepare_build('rnv', started='2017-10-10 11:00:00')
        self.verify_priority(None)

    def test_no_build(self, _):
        pkg = self.prepare_packages('foo')[0]
        pkg.resolved = True
        self.verify_priority(None, pkg)

    def test_resolution_not_attempted(self, _):
        self.pkg.resolved = None
        self.verify_priority(None)

    def test_resolution_skipped(self, _):
        self.pkg.resolved = None
        self.pkg.skip_resolution = True
        self.verify_priority(-30)


class StatsTest(DBTest):
    def add_task(self, build, arch, started, finished):
        koji_task = KojiTask(task_id=7541,
                             arch=arch,
                             state=1,
                             started=datetime.fromtimestamp(started),
                             finished=(datetime.fromtimestamp(finished) if finished else None),
                             build_id=build.id)
        self.db.add(koji_task)
        self.db.commit()

    def test_time_consumption_per_package(self):
        rnv = self.prepare_build('rnv')
        self.add_task(rnv, 'x86_64', 123, 456)
        self.add_task(rnv, 'aarch64', 125, 666)
        # Before refresh MV should be empty
        self.assertEqual(0, self.db.query(ResourceConsumptionStats).count())
        # After refresh it should contain some entries
        self.db.refresh_materialized_view(ResourceConsumptionStats)
        self.assertEqual(2, self.db.query(ResourceConsumptionStats).count())
        # Now add more data
        self.add_task(rnv, 'x86_64', 1000, 1100)
        self.add_task(rnv, 'x86_64', 2000, 2500)
        self.add_task(rnv, 'x86_64', 5000, None)
        self.add_task(self.prepare_build('xpp3'), 'x86_64', 111, 444)
        self.add_task(self.prepare_build('junit'), 'noarch', 24, 42)
        # Until it's refreshed again, MV should still contain only 2 rows
        self.assertEqual(2, self.db.query(ResourceConsumptionStats).count())
        self.db.refresh_materialized_view(ResourceConsumptionStats)
        self.assertEqual(4, self.db.query(ResourceConsumptionStats).count())
        stats = self.db.query(ResourceConsumptionStats).order_by(ResourceConsumptionStats.time).all()
        self.assertEqual('junit', stats[0].name)
        self.assertEqual('noarch', stats[0].arch)
        self.assertEqual(timedelta(0, 42 - 24), stats[0].time)
        self.assertAlmostEqual(0.0099, stats[0].time_percentage, 4)
        self.assertEqual('xpp3', stats[1].name)
        self.assertEqual('x86_64', stats[1].arch)
        self.assertEqual(timedelta(0, 333), stats[1].time)
        self.assertAlmostEqual(0.1825, stats[1].time_percentage, 4)
        self.assertEqual('rnv', stats[2].name)
        self.assertEqual('aarch64', stats[2].arch)
        self.assertEqual(timedelta(0, 666 - 125), stats[2].time)
        self.assertAlmostEqual(0.2964, stats[2].time_percentage, 4)
        self.assertEqual('rnv', stats[3].name)
        self.assertEqual('x86_64', stats[3].arch)
        self.assertEqual(timedelta(0, 333 + 100 + 500), stats[3].time)
        self.assertAlmostEqual(0.5112, stats[3].time_percentage, 4)

    def test_time_consumption_only_running(self):
        rnv = self.prepare_build('rnv')
        self.add_task(rnv, 'x86_64', 123, None)
        self.db.refresh_materialized_view(ResourceConsumptionStats)
        self.assertEqual(1, self.db.query(ResourceConsumptionStats).count())
        stats = self.db.query(ResourceConsumptionStats).one()
        self.assertEqual('rnv', stats.name)
        self.assertEqual('x86_64', stats.arch)
        self.assertIsNone(stats.time)
        self.assertIsNone(stats.time_percentage)

    def test_package_counts(self):
        self.db.refresh_materialized_view(ScalarStats)
        stats = self.db.query(ScalarStats).one()
        self.assertEqual(0, stats.packages)
        self.prepare_packages('rnv')[0].tracked = False
        self.prepare_packages('junit')[0].blocked = True
        self.prepare_packages('xpp3')
        self.db.refresh_materialized_view(ScalarStats)
        stats = self.db.query(ScalarStats).one()
        self.assertEqual(3, stats.packages)
        self.assertEqual(2, stats.tracked_packages)
        self.assertEqual(1, stats.blocked_packages)

    def test_build_counts(self):
        for i in range(0, 7):
            self.prepare_build('rnv', True).real = True
        for i in range(0, 5):
            self.prepare_build('rnv', False)
        for i in range(0, 4):
            self.prepare_build('rnv', None)
        self.db.refresh_materialized_view(ScalarStats)
        stats = self.db.query(ScalarStats).one()
        self.assertEqual(16, stats.builds)
        self.assertEqual(7, stats.real_builds)
        self.assertEqual(9, stats.scratch_builds)
