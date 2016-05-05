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

from common import DBTest
from koschei.models import Build

class TriggerTest(DBTest):

    def test_new(self):
        [p, e] = self.prepare_packages(['rnv', 'eclipse'])
        [b] = self.prepare_builds(123, rnv=None)
        self.assertIsNone(p.last_complete_build_id)
        self.assertIsNone(p.last_complete_build_state)
        self.assertIsNone(e.last_complete_build_id)
        self.assertIsNone(e.last_complete_build_state)
        self.assertEqual(b.id, p.last_build_id)
        self.assertIsNone(e.last_build_id)

    def test_update(self):
        [p, _] = self.prepare_packages(['rnv', 'eclipse'])
        [b] = self.prepare_builds(123, rnv=None)
        self.assertEqual(b.id, p.last_build_id)
        b.state = Build.FAILED
        self.s.commit()
        self.assertEqual(b.id, p.last_build_id)
        self.assertEqual(b.id, p.last_complete_build_id)
        self.assertEqual(b.state, p.last_complete_build_state)

    def test_complete(self):
        [p, e] = self.prepare_packages(['rnv', 'eclipse'])
        [b] = self.prepare_builds(123, rnv=True)
        self.assertEqual(b.id, p.last_complete_build_id)
        self.assertEqual(b.state, p.last_complete_build_state)
        self.assertEqual(b.id, p.last_build_id)
        self.assertIsNone(e.last_complete_build_id)
        self.assertIsNone(e.last_complete_build_state)
        self.assertIsNone(e.last_build_id)

    def test_failed(self):
        [p, e] = self.prepare_packages(['rnv', 'eclipse'])
        [b] = self.prepare_builds(123, eclipse=False)
        self.assertEqual(b.id, e.last_complete_build_id)
        self.assertEqual(b.state, e.last_complete_build_state)
        self.assertIsNone(p.last_complete_build_id)
        self.assertIsNone(p.last_complete_build_state)

    def test_running(self):
        [p, e] = self.prepare_packages(['rnv', 'eclipse'])
        [be, br] = self.prepare_builds(123, eclipse=False, rnv=True)
        [b3] = self.prepare_builds(126, eclipse=None)

        self.assertEqual(br.id, p.last_complete_build_id)
        self.assertEqual(br.state, p.last_complete_build_state)
        self.assertEqual(br.id, p.last_build_id)
        self.assertEqual(be.id, e.last_complete_build_id)
        self.assertEqual(be.state, e.last_complete_build_state)
        self.assertEqual(b3.id, e.last_build_id)

    def test_delete_new(self):
        [e] = self.prepare_packages(['eclipse'])
        [b1] = self.prepare_builds(123, eclipse=False)
        [b2] = self.prepare_builds(126, eclipse=True)
        [b3] = self.prepare_builds(127, eclipse=None)
        self.assertEqual(b3.id, e.last_build_id)
        self.s.delete(b1)
        self.s.commit()
        self.assertEqual(b3.id, e.last_build_id)
        self.s.delete(b3)
        self.s.commit()
        self.assertEqual(b2.id, e.last_build_id)
        self.s.delete(b2)
