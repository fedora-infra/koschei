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

# pylint: disable=unbalanced-tuple-unpacking,blacklisted-name

import six

from test.common import DBTest
from koschei.models import PackageGroup, PackageGroupRelation
from koschei import data


class DataTest(DBTest):
    def test_set_group_contents(self):
        group = PackageGroup(name='foo')
        bar, a1, a2, a3 = self.prepare_packages(['bar', 'a1', 'a2', 'a3'])
        self.db.add(group)
        self.db.flush()
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.db, group, content, append=False)

        six.assertCountEqual(self, [a1.base_id, a2.base_id, a3.base_id],
                              self.db.query(PackageGroupRelation.base_id)
                              .filter_by(group_id=group.id).all_flat())

    def test_append_group_content(self):
        group = PackageGroup(name='foo')
        self.db.add(group)
        self.db.flush()
        bar, a1, a2, a3 = self.prepare_packages(['bar', 'a1', 'a2', 'a3'])
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.db, group, content, append=True)

        six.assertCountEqual(self, [bar.base_id, a1.base_id, a2.base_id, a3.base_id],
                              self.db.query(PackageGroupRelation.base_id)
                              .filter_by(group_id=group.id).all_flat())
