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

from test.common import DBTest
from koschei.models import AdminNotice, Build, PackageGroup
from koschei.admin import main


class AdminTest(DBTest):
    def call_command(self, args):
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
