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

import time
from datetime import datetime

from test.common import DBTest
from koschei.models import (
    PackageGroup, PackageGroupRelation, Collection, Package, AppliedChange,
    KojiTask, ResolutionChange, ResolutionProblem, Dependency
)
from koschei import data


class DataTest(DBTest):
    def test_set_group_contents(self):
        group = PackageGroup(name='foo')
        bar, a1, a2, a3 = self.prepare_packages('bar', 'a1', 'a2', 'a3', 'a4')[:4]
        self.db.add(group)
        self.db.flush()
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.session, group, content, append=False)

        self.assertCountEqual([a1.base_id, a2.base_id, a3.base_id],
                             self.db.query(PackageGroupRelation.base_id)
                             .filter_by(group_id=group.id).all_flat())
        self.assert_action_log(
            "Group foo modified: package a1 added",
            "Group foo modified: package a2 added",
            "Group foo modified: package a3 added",
            "Group foo modified: package bar removed",
        )

    def test_append_group_content(self):
        group = PackageGroup(name='foo')
        self.db.add(group)
        self.db.flush()
        bar, a1, a2, a3 = self.prepare_packages('bar', 'a1', 'a2', 'a3', 'a4')[:4]
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1', 'a2', 'a3']
        data.set_group_content(self.session, group, content, append=True)

        self.assertCountEqual([bar.base_id, a1.base_id, a2.base_id, a3.base_id],
                             self.db.query(PackageGroupRelation.base_id)
                             .filter_by(group_id=group.id).all_flat())
        self.assert_action_log(
            "Group foo modified: package a1 added",
            "Group foo modified: package a2 added",
            "Group foo modified: package a3 added",
        )

    def test_delete_group_content(self):
        group = PackageGroup(name='foo')
        self.db.add(group)
        self.db.flush()
        bar, a1, = self.prepare_packages('bar', 'a1', 'a2', 'a3')[:2]
        rel = PackageGroupRelation(group_id=group.id, base_id=bar.base_id)
        self.db.add(rel)
        self.db.commit()
        rel = PackageGroupRelation(group_id=group.id, base_id=a1.base_id)
        self.db.add(rel)
        self.db.commit()
        content = ['a1']
        data.set_group_content(self.session, group, content, delete=True)

        self.assertCountEqual([bar.base_id],
                             self.db.query(PackageGroupRelation.base_id)
                             .filter_by(group_id=group.id).all_flat())
        self.assert_action_log(
            "Group foo modified: package a1 removed"
        )

    def test_set_group_contents_nonexistent(self):
        group = PackageGroup(name='foo')
        self.prepare_packages('bar')
        self.db.add(group)
        self.db.flush()
        with self.assertRaises(data.PackagesDontExist) as exc:
            data.set_group_content(self.session, group, ['bar', 'a1', 'a2'])
        self.assertCountEqual({'a1', 'a2'}, exc.exception.packages)

    def test_track_packages(self):
        foo, bar = self.prepare_packages('foo', 'bar')
        foo.tracked = False
        bar.tracked = False
        self.db.commit()
        data.track_packages(self.session, self.collection, ['bar'])
        self.db.commit()
        self.assertFalse(foo.tracked)
        self.assertTrue(bar.tracked)
        self.assert_action_log("Package bar (collection f25): tracked set from False to True")

    def test_track_packages_nonexistent(self):
        with self.assertRaises(data.PackagesDontExist):
            data.track_packages(self.session, self.collection, ['bar'])

    def test_copy_collection(self):
        self.prepare_build('rnv')
        self.prepare_build('eclipse')
        # the next build is old and shouldn't be copied
        base_maven_ancient = self.prepare_build(
            package='maven',
            state=True,
            arches=('ppc', 'sparc'),
            started='2000-01-01',
        )
        base_maven_quondam = self.prepare_build(
            package='maven',
            state=True,
            arches=('ppc', 'sparc'),
            started=datetime.fromtimestamp(time.time() - 29*24*60*60),
            real=True,
        )
        base_maven_current = self.prepare_build(
            package='maven',
            state=True,
            arches=('ppc', 'sparc'),
            started=datetime.fromtimestamp(time.time() - 10),
        )
        base_maven_running = self.prepare_build(
            package='maven',
            state=None,
            arches=('ppc', 'sparc'),
            started=datetime.now(),
        )
        self.prepare_depchange(dep_name='foo',
                               prev_epoch=1, prev_version='v1', prev_release='r1',
                               curr_epoch=2, curr_version='v2', curr_release='r2',
                               build_id=base_maven_current.id, distance=3)
        base_maven = base_maven_current.package
        self.prepare_resolution_change(base_maven, ["It's broken"])

        base = self.collection
        fork = self.prepare_collection('fork')
        data.copy_collection(self.session, base, fork)
        self.db.commit()

        fork_maven = self.db.query(Package).filter_by(collection=fork, name='maven').one()
        self.assertIsNotNone(fork_maven)
        self.assertNotEqual(base_maven.id, fork_maven.id)
        self.assertEqual(base.id, base_maven.collection_id)
        self.assertEqual(fork.id, fork_maven.collection_id)
        self.assertEqual('maven', fork_maven.name)

        self.assertEqual(3, len(fork_maven.all_builds))
        fork_maven_running, fork_maven_current, fork_maven_quondam = fork_maven.all_builds
        self.assertEqual(6, len(set((
            base_maven_running.id, fork_maven_running.id,
            base_maven_current.id, fork_maven_current.id,
            base_maven_quondam.id, fork_maven_quondam.id,
        ))))
        self.assertEqual(base_maven_running.id, base_maven.last_build_id)
        self.assertEqual(base_maven_current.id, base_maven.last_complete_build_id)
        self.assertEqual(fork_maven_running.id, fork_maven.last_build_id)
        self.assertEqual(fork_maven_current.id, fork_maven.last_complete_build_id)

        self.assertNotEqual(base_maven_running.build_arch_tasks[0].id,
                            fork_maven_running.build_arch_tasks[0].id)
        self.assertEqual(base_maven_running.build_arch_tasks[0].task_id,
                         fork_maven_running.build_arch_tasks[0].task_id)
        self.assertEqual('open', fork_maven_running.build_arch_tasks[0].state_string)

        self.assertEqual(1, len(fork_maven_current.dependency_changes))
        self.assertEqual('r2', fork_maven_current.dependency_changes[0].curr_dep.release)

        self.assertEqual("It's broken", self.db.query(ResolutionChange).
                         filter_by(package_id=fork_maven.id).one().problems[0].problem)
