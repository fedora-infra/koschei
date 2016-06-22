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

import koschei.models as m

from .common import DBTest


class ModelTest(DBTest):

    def test_group_cardinality(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        self.assertEqual(3, group.package_count)

    def test_group_cardinality_multiple_collections(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        new_collection = m.Collection(name="new", display_name="New", target_tag="tag2",
                                      build_tag="build_tag2", priority_coefficient=2.0)
        self.s.add(new_collection)
        self.s.commit()
        self.s.add(m.Package(name='bar', collection_id=new_collection.id))
        self.s.commit()
        self.assertEqual(3, group.package_count)

    def test_group_cardinality_blocked(self):
        group = self.prepare_group('xyzzy', content=['foo', 'bar', 'baz'])
        self.prepare_packages(['bar'])[0].blocked = True
        self.s.commit()
        self.assertEqual(2, group.package_count)

    def test_group_cardinality_partially_blocked(self):
        # Package xalan-j2 is blocked in one collection only.
        group = self.prepare_group('xyzzy', content=['xalan-j2'])
        self.prepare_packages(['xalan-j2'])[0].blocked = True
        self.s.commit()
        new_collection = m.Collection(name="new", display_name="New", target_tag="tag2",
                                      build_tag="build_tag2", priority_coefficient=2.0)
        self.s.add(new_collection)
        self.s.commit()
        self.s.add(m.Package(name='xalan-j2', collection_id=new_collection.id))
        self.s.commit()
        self.assertEqual(1, group.package_count)

    def test_group_cardinality_fully_blocked(self):
        # Package xalan-j2 is blocked in all collections.
        group = self.prepare_group('xyzzy', content=['xalan-j2'])
        self.prepare_packages(['xalan-j2'])[0].blocked = True
        self.s.commit()
        new_collection = m.Collection(name="new", display_name="New", target_tag="tag2",
                                      build_tag="build_tag2", priority_coefficient=2.0)
        self.s.add(new_collection)
        self.s.commit()
        self.s.add(m.Package(name='xalan-j2', collection_id=new_collection.id, blocked=True))
        self.s.commit()
        self.assertEqual(0, group.package_count)
