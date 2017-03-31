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

from koschei.models import Package, Collection, Build
from test.common import DBTest


class GroupTest(DBTest):

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
                                    target='foo', dest_tag="tag2",
                                    build_tag="build_tag2",
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
                                    target='foo', dest_tag="tag2",
                                    build_tag="build_tag2",
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
                                    target='foo', dest_tag="tag2",
                                    build_tag="build_tag2",
                                    priority_coefficient=2.0)
        self.db.add(new_collection)
        self.db.commit()
        pkg = Package(name='xalan-j2', collection_id=new_collection.id, blocked=True)
        self.ensure_base_package(pkg)
        self.db.add(pkg)
        self.db.commit()
        self.assertEqual(0, group.package_count)


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

    def assert_priority(self, expected, pkg=None):
        pkg = pkg or self.pkg
        self.db.commit()
        self.assertAlmostEqual(expected, self.get_priority(pkg))
        self.assertAlmostEqual(expected, self.get_priority_join(pkg))

    def test_basic(self, _):
        # time priority for just completed build, no other values
        self.assert_priority(-30)

    def test_coefficient(self, _):
        self.pkg.manual_priority = 10
        self.pkg.static_priority = 20
        self.pkg.dependency_priority = 40
        self.pkg.build_priority = 50
        self.pkg.collection.priority_coefficient = 0.5
        self.assert_priority(10 + 20 + 0.5 * (-30 + 40 + 50))

    def test_time(self, _):
        # 2 h difference
        self.build.started = '2017-10-10 08:00:00'
        self.assert_priority(-30)
        # 10 h difference
        self.build.started = '2017-10-10 00:00:00'
        self.assert_priority(39.2446980024098)
        # 1 day difference
        self.build.started = '2017-10-9 00:00:00'
        self.assert_priority(133.26248998925)
        # 1 month difference
        self.build.started = '2017-9-10 00:00:00'
        self.assert_priority(368.863607520133)

    def test_untracked(self, _):
        self.pkg.tracked = False
        self.assert_priority(None)

    def test_blocked(self, _):
        self.pkg.blocked = True
        self.assert_priority(None)

    def test_unresolved(self, _):
        self.pkg.resolved = False
        self.assert_priority(None)

    def test_running_build(self, _):
        self.prepare_build('rnv')
        self.assert_priority(None)

    def test_no_build(self, _):
        pkg = self.prepare_packages('foo')[0]
        pkg.resolved = True
        self.assert_priority(None, pkg)
