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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import absolute_import

from koschei.plugin import load_plugins

from test.common import AbstractTest, py3_only


class PluginTest(AbstractTest):
    def test_load_plugins(self):
        load_plugins('frontend', ['pagure'])

    @py3_only
    def test_load_plugin_nonexistent(self):
        with self.assertRaisesRegex(RuntimeError, 'xyzzy_plugin enabled but not installed'):
            load_plugins('frontend', ['xyzzy'])

    def test_load_plugin_nonexistent_endpoint(self):
        load_plugins('xyzzy', ['pagure'])

    def test_load_plugin_different_endpoints(self):
        load_plugins('backend', ['pagure'])
        load_plugins('frontend', ['pagure'])
