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

from unittest import skip
from datetime import datetime

from flask import json

from koschei.models import Collection
from test.frontend_common import FrontendTest


class ApiTest(FrontendTest):
    def setUp(self):
        super(ApiTest, self).setUp()
        self.task_id_counter = 1337

    def prepare_multiple(self):
        self.prepare_build('xpp3', True)
        self.prepare_build('xpp', None)
        self.prepare_build('xpp2', False)
        self.collection = Collection(
            name="epel7", display_name="EPEL 7", target="epel7",
            dest_tag='epel7', build_tag="epel7-build", priority_coefficient=0.2,
            latest_repo_resolved=False, latest_repo_id=456,
        )
        self.db.add(self.collection)
        self.db.commit()
        self.prepare_build('rnv', True)
        self.prepare_build('fop', False)

    def api_call(self, route):
        reply = self.client.get('/api/v1/' + route)
        self.assertEqual(200, reply.status_code)
        self.assertEqual('application/json', reply.content_type)
        return json.loads(reply.data)

    def assert_package(self, package, **kwargs):
        if kwargs['last_task_id']:
            kwargs['last_complete_build'] = {
                'task_id': kwargs['last_task_id'],
                'time_started': datetime.fromtimestamp(kwargs['last_task_id']).isoformat(),
                'time_finished': None,
                'epoch': None,
                'version': '1',
                'release': '1.fc25',
            }
        else:
            kwargs['last_complete_build'] = None
        del kwargs['last_task_id']
        self.assertDictEqual(package, kwargs)

    def test_packages_empty(self):
        packages = self.api_call('packages')
        self.assertListEqual([], packages)

    def test_package_no_build(self):
        self.prepare_packages('rnv')
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='unknown',
                            last_task_id=None)

    def test_package_ok(self):
        self.prepare_build('rnv', True)
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='ok',
                            last_task_id=1337)

    def test_package_failing(self):
        self.prepare_build('rnv', False)
        [rnv] = self.api_call('packages')
        self.assert_package(rnv, name='rnv', collection='f25', state='failing',
                            last_task_id=1337)

    def test_multiple(self):
        self.prepare_multiple()
        [fop, rnv, xpp, xpp2, xpp3] = self.api_call('packages')
        self.assert_package(fop, name='fop', collection='epel7',
                            state='failing', last_task_id=1341)
        self.assert_package(rnv, name='rnv', collection='epel7', state='ok',
                            last_task_id=1340)
        self.assert_package(xpp, name='xpp', collection='f25', state='unknown',
                            last_task_id=None)
        self.assert_package(xpp2, name='xpp2', collection='f25',
                            state='failing', last_task_id=1339)
        self.assert_package(xpp3, name='xpp3', collection='f25', state='ok',
                            last_task_id=1337)

    def test_collection_filtering(self):
        self.prepare_multiple()
        [fop, rnv] = self.api_call('packages?collection=epel7')
        self.assert_package(fop, name='fop', collection='epel7',
                            state='failing', last_task_id=1341)
        self.assert_package(rnv, name='rnv', collection='epel7', state='ok',
                            last_task_id=1340)

    def test_collection_filtering_non_existent(self):
        self.prepare_multiple()
        # FIXME this should return 200 and empty list instead of 404
        reply = self.client.get('/api/v1/packages?collection=xyzzy')
        self.assertEqual(404, reply.status_code)
        # result = self.api_call('packages?collection=xyzzy')
        # self.assertListEqual([], result)

    def test_collection_filtering_multiple(self):
        self.prepare_multiple()
        [fop, rnv, xpp, xpp2, xpp3] = (
            self.api_call('packages?collection=epel7&collection=xyzzy&collection=f25')
        )
        self.assert_package(fop, name='fop', collection='epel7',
                            state='failing', last_task_id=1341)
        self.assert_package(rnv, name='rnv', collection='epel7', state='ok',
                            last_task_id=1340)
        self.assert_package(xpp, name='xpp', collection='f25', state='unknown',
                            last_task_id=None)
        self.assert_package(xpp2, name='xpp2', collection='f25',
                            state='failing', last_task_id=1339)
        self.assert_package(xpp3, name='xpp3', collection='f25', state='ok',
                            last_task_id=1337)

    def test_package_filtering(self):
        self.prepare_multiple()
        [xpp] = self.api_call('packages?name=xpp')
        self.assert_package(xpp, name='xpp', collection='f25', state='unknown',
                            last_task_id=None)

    def test_package_filtering_non_existent(self):
        self.prepare_multiple()
        result = self.api_call('packages?name=maven-project-info-reports-plugin')
        self.assertListEqual([], result)

    def test_package_filtering_multiple(self):
        self.prepare_multiple()
        [rnv, xpp] = self.api_call('packages?name=rnv&name=xpp&name=maven')
        self.assert_package(rnv, name='rnv', collection='epel7', state='ok',
                            last_task_id=1340)
        self.assert_package(xpp, name='xpp', collection='f25', state='unknown',
                            last_task_id=None)

    def test_compound_filtering(self):
        self.prepare_multiple()
        [fop, rnv] = self.api_call('packages?name=rnv&name=fop&collection=epel7')
        self.assert_package(fop, name='fop', collection='epel7',
                            state='failing', last_task_id=1341)
        self.assert_package(rnv, name='rnv', collection='epel7', state='ok',
                            last_task_id=1340)

    @skip
    def test_collections_diff(self):
        self.prepare_build('good', True)
        self.prepare_build('bad', False)
        self.prepare_build('broken', True)
        self.prepare_build('fixed', False)
        self.db.commit()
        self.collection = Collection(
            name="epel7", display_name="EPEL 7", target="epel7",
            dest_tag='epel7', build_tag="epel7-build", priority_coefficient=0.2,
            latest_repo_resolved=False, latest_repo_id=456,
        )
        self.db.add(self.collection)
        self.prepare_build('good', True)
        self.prepare_build('bad', False)
        self.prepare_build('broken', False)
        self.prepare_build('fixed', True)
        [broken, fixed] = self.api_call('collections/diff/f25/epel7')
        self.assertDictEqual(broken, {
            'name': 'broken',
            'state': {'f25': 'ok', 'epel7': 'failing'}
        })
        self.assertDictEqual(fixed, {
            'name': 'fixed',
            'state': {'f25': 'failing', 'epel7': 'ok'}
        })
