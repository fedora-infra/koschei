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

from test.common import DBTest

from koschei.locks import (
    pg_lock, pg_session_lock, Locked, LOCK_REPO_RESOLVER, LOCK_BUILD_RESOLVER,
)


class LocksTest(DBTest):
    def setUp(self):
        super().setUp()
        self.s1 = self.create_session()
        self.s2 = self.create_session()

    def tearDown(self):
        super().tearDown()
        self.s1.close()
        self.s2.close()

    def test_transaction_lock(self):
        pg_lock(self.s1.db, LOCK_REPO_RESOLVER, 1, block=False, transaction=True)
        pg_lock(self.s2.db, LOCK_BUILD_RESOLVER, 1, block=False, transaction=True)
        with self.assertRaises(Locked):
            pg_lock(self.s2.db, LOCK_REPO_RESOLVER, 1, block=False, transaction=True)

    def test_session_lock(self):
        with pg_session_lock(self.s1.db, LOCK_REPO_RESOLVER, 1, block=False):
            with pg_session_lock(self.s2.db, LOCK_BUILD_RESOLVER, 1, block=False):
                with self.assertRaises(Locked):
                    with pg_session_lock(self.s2.db, LOCK_REPO_RESOLVER, 1, block=False):
                        pass
        # Try if it's unlocked
        with pg_session_lock(self.s2.db, LOCK_REPO_RESOLVER, 1, block=False):
            pass
