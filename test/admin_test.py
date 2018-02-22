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

from test.common import DBTest
from koschei.models import AdminNotice, Build
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
