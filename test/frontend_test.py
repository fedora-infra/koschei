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

from functools import wraps

# pylint:disable = unused-import
import koschei.frontend.views
import koschei.frontend.auth

from koschei.frontend import app
from koschei.models import User, PackageGroup

from test.common import DBTest


def authenticate(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        self = args[0]
        self.prepare_user(name='jdoe', admin=False)
        self.client.get('login')
        return fn(*args, **kwargs)
    return decorated


def authenticate_admin(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        self = args[0]
        self.prepare_user(name='admin', admin=True)
        self.client.get('login')
        return fn(*args, **kwargs)
    return decorated


class FrontendTest(DBTest):
    def setUp(self):
        super(FrontendTest, self).setUp()
        app.config['TESTING'] = True
        app.config['CSRF_ENABLED'] = False  # older versions of flask-wtf (EPEL 7)
        app.config['WTF_CSRF_ENABLED'] = False  # newer versions of flask-wtf (Fedora)
        self.client = app.test_client()

    def test_main_page(self):
        reply = self.client.get('/')
        self.assertEqual(200, reply.status_code)
        self.assertEqual('text/html; charset=utf-8', reply.content_type)
        normalized_data = ' '.join(reply.data.decode('utf-8').split())
        self.assertIn('<!DOCTYPE html>', normalized_data)
        self.assertIn('Packages from 1 to 0 from total 0', normalized_data)
        self.assertIn('Package summary', normalized_data)

    def test_404(self):
        reply = self.client.get('/xyzzy')
        self.assertEqual(404, reply.status_code)

    def test_static(self):
        reply = self.client.get('/static/koschei.css')
        self.assertEqual(200, reply.status_code)
        self.assertEqual('text/css; charset=utf-8', reply.content_type)

    def test_documentation(self):
        reply = self.client.get('documentation')
        self.assertEqual(200, reply.status_code)
        self.assertIn('How it works?', reply.data.decode('utf-8'))

    def test_login(self):
        reply = self.client.get('login')
        self.assertEqual(302, reply.status_code)
        self.assertEqual('http://localhost/', reply.location)

    @authenticate_admin
    def test_cancel_build(self):
        self.prepare_packages('groovy')
        build = self.prepare_build('groovy')
        url = 'build/{0}/cancel'.format(build.id)
        reply = self.client.post(url, follow_redirects=True)
        self.assertEqual(200, reply.status_code)
        self.assertIn('Cancelation request sent.', reply.data.decode('utf-8'))
        self.db.expire(build)
        self.assertTrue(build.cancel_requested)

    @authenticate
    def test_cancel_build_unauthorized(self):
        self.prepare_packages('groovy')
        build = self.prepare_build('groovy')
        url = 'build/{0}/cancel'.format(build.id)
        reply = self.client.post(url, follow_redirects=True)
        self.assertEqual(403, reply.status_code)
        self.db.expire(build)
        self.assertFalse(build.cancel_requested)

    @authenticate_admin
    def test_cancel_build_not_running(self):
        self.prepare_packages('groovy')
        build = self.prepare_build('groovy', True)
        url = 'build/{0}/cancel'.format(build.id)
        reply = self.client.post(url, follow_redirects=True)
        self.assertEqual(200, reply.status_code)
        self.assertIn('Only running builds can be canceled.', reply.data.decode('utf-8'))
        self.db.expire(build)
        self.assertFalse(build.cancel_requested)

    @authenticate_admin
    def test_cancel_build_pending(self):
        self.prepare_packages('groovy')
        build = self.prepare_build('groovy')
        build.cancel_requested = True
        self.db.commit()
        url = 'build/{0}/cancel'.format(build.id)
        reply = self.client.post(url, follow_redirects=True)
        self.assertEqual(200, reply.status_code)
        self.assertIn('Build already has pending cancelation request.',
                      reply.data.decode('utf-8'))
        self.db.expire(build)
        self.assertTrue(build.cancel_requested)

    @authenticate
    def test_add_package_nonexistent(self):
        reply = self.client.post(
            'add-packages',
            data=dict(packages='SimplyHTML', collection=self.collection.name),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertIn('Packages don&#39;t exist: SimplyHTML', reply.data.decode('utf-8'))

    @authenticate
    def test_add_package(self):
        pkg = self.prepare_packages('xpp3')[0]
        pkg.tracked = False
        self.db.commit()
        reply = self.client.post(
            'add-packages',
            data=dict(packages='xpp3', collection=self.collection.name),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertIn('Packages added: xpp3', reply.data.decode('utf-8'))
        self.assertTrue(pkg.tracked)
