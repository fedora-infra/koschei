# Copyright (C) 2018  Red Hat, Inc.
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

import shlex

from datetime import datetime
from tempfile import NamedTemporaryFile
from mock import patch

from sqlalchemy.exc import InvalidRequestError

from test.common import DBTest, KoscheiMockSessionMixin, with_koji_cassette
from koschei.models import (
    AdminNotice, Build, PackageGroup, Collection, Package, CollectionGroup,
)
from koschei.admin import main, KoscheiAdminSession


class KoscheiAdminSessionMock(KoscheiMockSessionMixin, KoscheiAdminSession):
    pass


class AdminTest(DBTest):
    def create_session(self):
        return KoscheiAdminSessionMock(self)

    def call_command(self, args):
        self.db.commit()
        if isinstance(args, str):
            args = shlex.split(args)
        main(args=args, session=self.session)

    def test_set_and_clear_notice(self):
        self.call_command('set-notice foo')
        notices = self.db.query(AdminNotice).all()
        self.assertEqual(1, len(notices))
        self.assertEqual('global_notice', notices[0].key)
        self.assertEqual('foo', notices[0].content)
        self.call_command('clear-notice')
        notices = self.db.query(AdminNotice).all()
        self.assertEqual(0, len(notices))
        self.assert_action_log("Admin notice added: foo", "Admin notice cleared")

    def test_cleanup(self):
        b1_id = self.prepare_build('rnv', state=True, started='2016-1-1').id
        b2_id = self.prepare_build('rnv', state=True, started=datetime.now()).id
        self.call_command('cleanup')
        b1 = self.db.query(Build).get(b1_id)
        b2 = self.db.query(Build).get(b2_id)
        self.assertIs(None, b1)
        self.assertIsNot(None, b2)

    def test_add_pkg(self):
        rnv = self.prepare_package('rnv', tracked=False)
        eclipse = self.prepare_package('eclipse', tracked=False)
        maven = self.prepare_package('maven', tracked=True)
        self.call_command('add-pkg -c f25 eclipse maven')
        self.assertFalse(rnv.tracked)
        self.assertTrue(eclipse.tracked)
        self.assertTrue(maven.tracked)
        self.assert_action_log(
            "Package eclipse (collection f25): tracked set from False to True",
        )

    def test_set_priority(self):
        rnv = self.prepare_package('rnv')
        eclipse = self.prepare_package('eclipse')
        eclipse.manual_priority = 3000
        self.call_command('set-priority --collection f25 rnv eclipse 2000')
        self.assertEqual(2000, rnv.manual_priority)
        self.assertEqual(2000, eclipse.manual_priority)
        self.assert_action_log(
            "Package rnv (collection f25): manual_priority set from 0 to 2000",
            "Package eclipse (collection f25): manual_priority set from 3000 to 2000",
        )

    def test_set_priority_nonexistent(self):
        rnv = self.prepare_package('rnv')
        with self.assertRaises(SystemExit, msg="Packages not found: eclipse"):
            self.call_command('set-priority --collection f25 rnv eclipse 2000')
        self.assertEqual(0, rnv.manual_priority)
        self.assert_action_log()

    def test_group_commands(self):
        self.call_command('create-group global-group')
        group1 = self.db.query(PackageGroup).filter_by(name='global-group').one()
        self.assertIs(None, group1.namespace)
        self.call_command('create-group me/my-packages')
        group2 = self.db.query(PackageGroup).filter_by(name='my-packages').one()
        self.assertEqual('me', group2.namespace)

        maven = self.prepare_package('maven')
        eclipse = self.prepare_package('eclipse')
        rnv = self.prepare_package('rnv')
        with NamedTemporaryFile() as fo:
            fo.write(b'maven eclipse')
            fo.flush()
            self.call_command(
                f'edit-group global-group --content-from-file {fo.name}'
            )
        self.assertCountEqual([maven.base, eclipse.base], group1.packages)
        with NamedTemporaryFile() as fo:
            fo.write(b'rnv')
            fo.flush()
            self.call_command(
                f'edit-group global-group --append --content-from-file {fo.name}'
            )
        self.assertCountEqual([maven.base, eclipse.base, rnv.base], group1.packages)
        with NamedTemporaryFile() as fo:
            fo.write(b'rnv')
            fo.flush()
            self.call_command(
                f'edit-group global-group --content-from-file {fo.name}'
            )
        self.assertCountEqual([rnv.base], group1.packages)
        with NamedTemporaryFile() as fo:
            fo.write(b'eclipse')
            fo.flush()
            self.call_command(
                f'edit-group me/my-packages --content-from-file {fo.name}'
            )
        self.assertCountEqual([eclipse.base], group2.packages)

    @with_koji_cassette
    def test_collection_commands(self):
        # test create collection
        self.call_command(
            'create-collection f28 -d"Fedora Rawhide" -t f28 -o 128 \
             --bugzilla-product Fedora --bugzilla-version 28'
        )
        collection = self.db.query(Collection).filter_by(name='f28').one()
        self.assertEqual("Fedora Rawhide", str(collection))
        self.assertEqual("f28", collection.target)
        self.assertEqual("f28-build", collection.build_tag)
        self.assertEqual("f28-build", collection.dest_tag)
        self.assertEqual("28", collection.bugzilla_version)

        rnv = self.prepare_package('rnv', collection=collection)

        # test edit collection
        self.call_command(
            'edit-collection f28 --bugzilla-version rawhide'
        )
        self.assertEqual("rawhide", collection.bugzilla_version)

        # add it to collection group
        self.call_command('create-collection-group fedora -d Fedora -c f28')
        group = self.db.query(CollectionGroup).one()
        self.assertEqual('fedora', group.name)
        self.assertEqual('Fedora', group.display_name)
        self.assertEqual('Fedora', str(group))
        self.assertEqual(1, len(group.collections))
        self.assertIs(collection, group.collections[0])

        # test fork collection
        self.call_command(
            'fork-collection f28 f28-side-42 -d"Side tag 42" -t f28-side-42'
        )
        forked = self.db.query(Collection).filter_by(name='f28-side-42').one()
        self.assertIsNot(forked, collection)
        self.assertEqual("f28-side-42", forked.target)
        self.assertEqual("f28-side-42", forked.build_tag)
        self.assertEqual("f28-side-42", forked.dest_tag)
        self.assertEqual("rawhide", forked.bugzilla_version)
        self.assertEqual(128, forked.order)
        self.assertEqual("f28", collection.target)
        self.assertEqual("f28-build", collection.build_tag)
        self.assertEqual("f28-build", collection.dest_tag)
        self.assertEqual("rawhide", collection.bugzilla_version)
        self.assertEqual(128, collection.order)
        rnv_forked = (
            self.db.query(Package)
            .filter_by(name='rnv', collection=forked)
            .one()
        )
        self.assertIsNot(rnv_forked, rnv)
        self.assertEqual(2, len(group.collections))
        self.assertCountEqual([collection, forked], group.collections)

        self.db.execute("DISCARD TEMP")

        # test branch collection
        self.call_command(
            'branch-collection f28 f29 -d"Fedora 28" -t f29 --bugzilla-version 28'
        )
        branched = self.db.query(Collection).filter_by(name='f28').one()
        self.assertIsNot(branched, collection)
        self.assertEqual("f28", branched.target)
        self.assertEqual("f28-build", branched.build_tag)
        self.assertEqual("f28-build", branched.dest_tag)
        self.assertEqual("28", branched.bugzilla_version)
        self.assertEqual(128, branched.order)
        self.assertEqual("f29", collection.target)
        self.assertEqual("f29-build", collection.build_tag)
        self.assertEqual("f29-build", collection.dest_tag)
        self.assertEqual("rawhide", collection.bugzilla_version)
        self.assertEqual(129, collection.order)
        rnv_branched = (
            self.db.query(Package)
            .filter_by(name='rnv', collection=branched)
            .one()
        )
        self.assertIsNot(rnv_branched, rnv)
        self.assertEqual(3, len(group.collections))
        self.assertCountEqual([collection, branched, forked], group.collections)

        # test delete collection
        self.call_command('delete-collection --force f28')
        with self.assertRaises(InvalidRequestError):
            self.db.refresh(branched)
        with self.assertRaises(InvalidRequestError):
            self.db.refresh(rnv_branched)

        self.assertEqual(2, len(group.collections))

        self.assert_action_log(
            "Collection f28 created",
            "Collection f28 modified",
            "Collection f28-side-42 forked from f28",
            "Collection f29 branched from f28",
            "Collection f28 deleted",
        )

    @with_koji_cassette
    def test_submit_build(self):
        self.collection = self.prepare_collection('f29')
        rnv = self.prepare_package('rnv')
        self.prepare_build('rnv', state='complete', version='1.7.11', release='15.fc28')
        with patch('koschei.backend.submit_build') as submit_build_mock:
            self.call_command('submit-build rnv')
            submit_build_mock.assert_called_once_with(
                self.session,
                rnv,
                arch_override={'x86_64', 'armv7hl', 'i686'},
            )
