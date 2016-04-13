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

import koschei.frontend
import koschei.frontend.views

from .common import DBTest


class FrontendTest(DBTest):
    def setUp(self):
        super(FrontendTest, self).setUp()
        self.app = koschei.frontend.app.test_client()

    def test_main_page(self):
        reply = self.app.get('/')
        self.assertEqual(200, reply.status_code)
        self.assertEqual('text/html; charset=utf-8', reply.content_type)
        normalized_data = ' '.join(reply.data.split())
        self.assertIn('<!DOCTYPE html>', normalized_data)
        self.assertIn('Packages from 1 to 0 from total 0', normalized_data)

    def test_404(self):
        reply = self.app.get('/xyzzy')
        self.assertEqual(404, reply.status_code)

    def test_static(self):
         reply = self.app.get('/static/koschei.css')
         self.assertEqual(200, reply.status_code)
         self.assertEqual('text/css; charset=utf-8', reply.content_type)
