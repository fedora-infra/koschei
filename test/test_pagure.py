# Copyright (C) 2017 Red Hat, Inc.
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

from __future__ import print_function, absolute_import

from koschei import plugin

from test.common import DBTest, my_vcr


class TestPagure(DBTest):
    def setUp(self):
        super(TestPagure, self).setUp()
        plugin.load_plugins('frontend', ['pagure'])

    @my_vcr.use_cassette('pagure_msimacek')
    def test_get_my_packages(self):
        results = []
        for r in plugin.dispatch_event('get_user_packages',
                                       self.session,
                                       username='msimacek'):
            results += r
        self.assertIn('rnv', results)
