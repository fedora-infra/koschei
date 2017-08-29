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

from datetime import datetime

from test.common import DBTest
from koschei.models import (
    PackageGroup, PackageGroupRelation, Collection, Package, AppliedChange,
    KojiTask, ResolutionChange, ResolutionProblem,
)
from koschei import data


class DataTest(DBTest):
    def test_set_group_contents(self):
        group = PackageGroup(name='foo')
        bar, a1, a2, a3 = self.prepare_packages('bar', 'a1', 'a2', 'a3')
        self.db.add(group)
        self.db.flush()
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.session, group, content, append=False)

        six.assertCountEqual(self, [a1.base_id, a2.base_id, a3.base_id],
                             self.db.query(PackageGroupRelation.base_id)
                             .filter_by(group_id=group.id).all_flat())

    def test_append_group_content(self):
        group = PackageGroup(name='foo')
        self.db.add(group)
        self.db.flush()
        bar, a1, a2, a3 = self.prepare_packages('bar', 'a1', 'a2', 'a3')
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.session, group, content, append=True)

        six.assertCountEqual(self, [bar.base_id, a1.base_id, a2.base_id, a3.base_id],
                             self.db.query(PackageGroupRelation.base_id)
                             .filter_by(group_id=group.id).all_flat())

    def test_set_group_contents_nonexistent(self):
        group = PackageGroup(name='foo')
        self.prepare_packages('bar')
        self.db.add(group)
        self.db.flush()
        with self.assertRaises(data.PackagesDontExist) as exc:
            data.set_group_content(self.session, group, ['bar', 'a1', 'a2'])
        six.assertCountEqual(self, {'a1', 'a2'}, exc.exception.packages)

    def test_track_packages(self):
        foo, bar = self.prepare_packages('foo', 'bar')
        foo.tracked = False
        bar.tracked = False
        self.db.commit()
        data.track_packages(self.session, self.collection, ['bar'])
        self.db.commit()
        self.assertFalse(foo.tracked)
        self.assertTrue(bar.tracked)

    def test_track_packages_nonexistent(self):
        with self.assertRaises(data.PackagesDontExist):
            data.track_packages(self.session, self.collection, ['bar'])

    def test_copy_collection(self):
        now = datetime.now()
        source = self.collection
        _, _, maven1 = self.prepare_packages('rnv', 'eclipse', 'maven')
        self.prepare_build('rnv')
        self.prepare_build('eclipse')
        # the next build is old and shouldn't be copied
        self.prepare_build('maven', state=True, started='2016-01-01')
        old_build1 = self.prepare_build('maven', state=True, started=now)
        new_build1 = self.prepare_build('maven', started=now)
        copy = Collection(
            name='copy', display_name='copy', target='a', build_tag='b',
            dest_tag='c',
        )
        self.db.add(copy)
        change1 = AppliedChange(
            dep_name='foo', build_id=old_build1.id,
            prev_version='1', prev_release='1',
            curr_version='1', curr_release='2',
        )
        self.db.add(change1)
        task1 = KojiTask(
            build_id=new_build1.id,
            task_id=new_build1.task_id,
            state=1,
            arch='x86_64',
            started=new_build1.started,
        )
        self.db.add(task1)
        rchange1 = ResolutionChange(
            package_id=maven1.id,
            resolved=False,
            timestamp=now,
        )
        self.db.add(rchange1)
        self.db.flush()
        problem1 = ResolutionProblem(
            resolution_id=rchange1.id,
            problem="It's broken",
        )
        self.db.add(problem1)
        self.db.commit()

        data.copy_collection(self.session, source, copy)
        self.db.commit()

        maven2 = self.db.query(Package).filter_by(collection=copy, name='maven').first()
        self.assertIsNotNone(maven2)
        self.assertNotEqual(maven1.id, maven2.id)
        self.assertEqual(source.id, maven1.collection_id)
        self.assertEqual(copy.id, maven2.collection_id)
        self.assertEqual('maven', maven2.name)

        self.assertEqual(2, len(maven2.all_builds))
        new_build2, old_build2 = maven2.all_builds
        self.assertNotEqual(new_build1.id, new_build2.id)
        self.assertNotEqual(old_build1.id, old_build2.id)
        self.assertEqual(new_build1.id, maven1.last_build_id)
        self.assertEqual(old_build1.id, maven1.last_complete_build_id)
        self.assertEqual(new_build2.id, maven2.last_build_id)
        self.assertEqual(old_build2.id, maven2.last_complete_build_id)
        self.assertEqual(1, new_build2.build_arch_tasks[0].state)

        self.assertEqual(1, len(old_build2.dependency_changes))
        change2 = old_build2.dependency_changes[0]
        self.assertEqual('2', change2.curr_release)

        rchange2 = self.db.query(ResolutionChange).filter_by(package_id=maven2.id).one()
        self.assertEqual("It's broken", rchange2.problems[0].problem)
