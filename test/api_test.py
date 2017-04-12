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

from flask import json
from frontend_test import FrontendTest


class ApiTest(FrontendTest):
    def setUp(self):
        super(ApiTest, self).setUp()
        self.task_id_counter = 1337

    def api_call(self, route):
        reply = self.client.get('/api/v1/' + route)
        self.assertEqual(200, reply.status_code)
        self.assertEqual('application/json', reply.content_type)
        return json.loads(reply.data)

    def assert_package(self, package, **kwargs):
        self.assertDictEqual(package, kwargs)

    def test_packages_empty(self):
        packages = self.api_call('packages')
        self.assertListEqual([], packages)

    def test_package_no_build(self):
        self.prepare_packages('rnv')
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='unknown', last_task_id=None)

    def test_package_ok(self):
        self.prepare_build('rnv', True)
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='ok', last_task_id=1337)

    def test_package_failing(self):
        self.prepare_build('rnv', False)
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='failing', last_task_id=1337)

    def test_multiple(self):
        self.prepare_build('xpp3', True)
        self.prepare_build('xpp', None)
        self.prepare_build('xpp2', False)
        [xpp, xpp2, xpp3] = self.api_call('packages')
        self.assert_package(xpp, name='xpp', collection='f25', state='unknown', last_task_id=None)
        self.assert_package(xpp2, name='xpp2', collection='f25', state='failing', last_task_id=1339)
        self.assert_package(xpp3, name='xpp3', collection='f25', state='ok', last_task_id=1337)
