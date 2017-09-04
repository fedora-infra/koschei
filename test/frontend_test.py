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

import re
from functools import wraps

# pylint:disable = unused-import
import koschei.frontend.api
import koschei.frontend.views
import koschei.frontend.auth

from koschei.frontend import app, db
from koschei.models import User, PackageGroup, AppliedChange

from test.common import DBTest


def login(client, user):
    client.get(
        'login',
        environ_base={
            'REMOTE_USER': user.name,
        },
    )


def authenticate(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        self = args[0]
        user = self.prepare_user(name='jdoe', admin=False)
        login(self.client, user)
        return fn(*args, **kwargs)
    return decorated


def authenticate_admin(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        self = args[0]
        user = self.prepare_user(name='admin', admin=True)
        login(self.client, user)
        return fn(*args, **kwargs)
    return decorated


class FrontendTest(DBTest):
    def get_session(self):
        return db

    def setUp(self):
        super(FrontendTest, self).setUp()
        self.assertIs(self.db, db)
        app.config['TESTING'] = True
        app.config['CSRF_ENABLED'] = False  # older versions of flask-wtf (EPEL 7)
        app.config['WTF_CSRF_ENABLED'] = False  # newer versions of flask-wtf (Fedora)
        self.client = app.test_client()
        self.teardown_appcontext_funcs = app.teardown_appcontext_funcs
        app.teardown_appcontext_funcs = []

    def tearDown(self):
        app.teardown_appcontext_funcs = self.teardown_appcontext_funcs
        app.do_teardown_appcontext()
        super(FrontendTest, self).tearDown()

    def assert_validated(self, reply):
        data = reply.data.decode('utf-8')
        match = re.search(r'Validation errors: [^<]*', data)
        if match:
            self.fail(match.group(0))

    def assert_validation_failed(self, reply, msg=None):
        data = reply.data.decode('utf-8')
        match = re.search(r'Validation errors: [^<]*', data)
        self.assertTrue(match, "Validation didn't fail")
        if msg:
            self.assertIn(msg, match.group(0))

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

    def test_create_group_unauth(self):
        reply = self.client.post(
            'add-group',
            data=dict(
                name='foo',
                packages='rnv,eclipse',
                owners='jdoe,user1',
            ),
            follow_redirects=True,
        )
        self.assertEqual(501, reply.status_code)
        self.assertEqual(
            0,
            self.db.query(PackageGroup).filter_by(name='foo').count(),
        )

    @authenticate
    def test_create_group(self):
        pkgs = set(self.prepare_packages('rnv', 'eclipse'))
        self.db.commit()
        reply = self.client.post(
            'add-group',
            data=dict(
                name='foo',
                packages='rnv,eclipse',
                owners='jdoe,user1',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertIn('foo', reply.data.decode('utf-8'))
        self.assertIn('eclipse', reply.data.decode('utf-8'))
        group = self.db.query(PackageGroup).filter_by(name='foo').one()
        self.assertEquals('jdoe', group.namespace)
        self.assertEquals({p.base for p in pkgs}, set(group.packages))
        self.assertEquals(
            {self.prepare_user(name='jdoe'), self.prepare_user(name='user1')},
            set(group.owners),
        )

    @authenticate
    def test_create_group_missing_pkg(self):
        self.prepare_packages('rnv')
        self.db.commit()
        reply = self.client.post(
            'add-group',
            data=dict(
                name='foo',
                packages='rnv,eclipse',
                owners='jdoe,user1',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertIn("Packages don&#39;t exist: eclipse", reply.data.decode('utf-8'))
        self.assertIn('Create new group', reply.data.decode('utf-8'))

    @authenticate
    def test_edit_group(self):
        pkgs = self.prepare_packages('rnv', 'eclipse', 'maven')
        group = self.prepare_group(
            namespace='jdoe',
            name='foo',
            owners=['jdoe', 'user1'],
            content=['rnv', 'eclipse'],
        )
        reply = self.client.post(
            'groups/jdoe/foo/edit',
            data=dict(
                name='bar',
                packages='eclipse,maven',
                owners='jdoe,user2',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertEqual(
            0,
            self.db.query(PackageGroup).filter_by(name='foo').count(),
        )
        group = self.db.query(PackageGroup).filter_by(name='bar').one()
        self.assertEquals('jdoe', group.namespace)
        self.assertEquals({p.base for p in pkgs[1:]}, set(group.packages))
        self.assertEquals(
            {self.prepare_user(name='jdoe'), self.prepare_user(name='user2')},
            set(group.owners),
        )

    @authenticate
    def test_edit_package(self):
        package = self.prepare_package('rnv')
        reply = self.client.post(
            'package/rnv/edit',
            data=dict(
                collection_id=package.collection_id,
                manual_priority=123,
                arch_override='x86_64, i386',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assert_validated(reply)
        self.assertEqual(123, package.manual_priority)
        self.assertEqual('x86_64 i386', package.arch_override)
        self.assertEqual(False, package.skip_resolution)

    @authenticate
    def test_edit_package_neg_arch_override(self):
        package = self.prepare_package('rnv')
        reply = self.client.post(
            'package/rnv/edit',
            data=dict(
                collection_id=package.collection_id,
                manual_priority=0,
                arch_override='^x86_64',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assert_validated(reply)
        self.assertEqual('^x86_64', package.arch_override)

    @authenticate
    def test_edit_package_unknown_arch_override(self):
        package = self.prepare_package('rnv')
        reply = self.client.post(
            'package/rnv/edit',
            data=dict(
                collection_id=package.collection_id,
                manual_priority=0,
                arch_override='asdf',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assert_validation_failed(reply, 'arch_override')
        self.assertEqual(None, package.arch_override)

    @authenticate
    def test_edit_package_skip_resolution(self):
        package = self.prepare_package('rnv')
        reply = self.client.post(
            'package/rnv/edit',
            data=dict(
                collection_id=package.collection_id,
                manual_priority=0,
                arch_override='',
                skip_resolution='on',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assert_validated(reply)
        self.assertEqual(True, package.skip_resolution)

    @authenticate
    def test_edit_group_unpermitted(self):
        pkgs = self.prepare_packages('rnv', 'eclipse', 'maven')
        group = self.prepare_group(
            namespace='user1',
            name='foo',
            owners=['user1'],
            content=['rnv', 'eclipse'],
        )
        reply = self.client.post(
            'groups/user1/foo/edit',
            data=dict(
                name='foo',
                packages='eclipse,maven',
                owners='jdoe,user2',
            ),
            follow_redirects=True,
        )
        self.assertEqual(200, reply.status_code)
        self.assertIn("You don&#39;t have permission", reply.data.decode('utf-8'))
        self.assertEquals('user1', group.namespace)
        self.assertEquals({p.base for p in pkgs[:2]}, set(group.packages))
        self.assertEquals(
            {self.prepare_user(name='user1')},
            set(group.owners),
        )

    @authenticate
    def test_delete_group(self):
        self.prepare_packages('rnv', 'eclipse')
        self.prepare_group(
            namespace='jdoe',
            name='foo',
            owners=['jdoe'],
            content=['rnv', 'eclipse'],
        )
        self.client.post(
            'groups/jdoe/foo/delete',
            follow_redirects=True,
        )
        self.assertEqual(
            0,
            self.db.query(PackageGroup).filter_by(name='foo').count(),
        )

    @authenticate
    def test_delete_group_unpermitted(self):
        self.prepare_packages('rnv', 'eclipse')
        self.prepare_group(
            namespace='user1',
            name='foo',
            owners=['user1'],
            content=['rnv', 'eclipse'],
        )
        self.client.post(
            'groups/user1/foo/delete',
            follow_redirects=True,
        )
        self.assertEqual(
            1,
            self.db.query(PackageGroup).filter_by(name='foo').count(),
        )

    def test_affected_by_unauthenticated(self):
        # bar was broken
        b1 = self.prepare_build('bar', True)
        b2 = self.prepare_build('bar', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='1.2', prev_release='3',
            curr_epoch=0, curr_version='4.5', curr_release='6',
        ))
        self.db.commit()
        reply = self.client.get(
            'affected-by/foo'+
            '?collection=f25' +
            '&epoch1=0' +
            '&version1=1.2' +
            '&release1=3' +
            '&epoch2=0' +
            '&version2=4.5' +
            '&release2=6'
        )
        self.assertEqual(302, reply.status_code)
        self.assertEqual("http://localhost/login?", reply.location[:23])

    @authenticate
    def test_affected_by_one(self):
        # bar was broken
        b1 = self.prepare_build('bar', True)
        b2 = self.prepare_build('bar', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='1.2', prev_release='3',
            curr_epoch=0, curr_version='4.5', curr_release='6',
        ))
        # baz was fixed
        b1 = self.prepare_build('baz', False)
        b2 = self.prepare_build('baz', True)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='1.2', prev_release='3',
            curr_epoch=0, curr_version='4.5', curr_release='6',
        ))
        # xyzzy failure is not related
        b1 = self.prepare_build('xyzzy', True)
        b2 = self.prepare_build('xyzzy', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='0.5', prev_release='1',
            curr_epoch=0, curr_version='0.7', curr_release='2',
        ))
        # abc was broken too
        b1 = self.prepare_build('abc', True)
        b2 = self.prepare_build('abc', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='0.5', prev_release='1',
            curr_epoch=0, curr_version='4.5', curr_release='6',
        ))
        # klm was broken too
        b1 = self.prepare_build('klm', True)
        b2 = self.prepare_build('klm', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='1.2', prev_release='3',
            curr_epoch=666, curr_version='0.7', curr_release='2',
        ))
        # ijk was broken too
        b1 = self.prepare_build('ijk', True)
        b2 = self.prepare_build('ijk', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='0.5', prev_release='1',
            curr_epoch=666, curr_version='0.7', curr_release='2',
        ))
        # pqr was broken too
        b1 = self.prepare_build('pqr', True)
        b2 = self.prepare_build('pqr', False)
        self.db.add(AppliedChange(
            build_id=b2.id,
            dep_name='foo', distance=3,
            prev_epoch=0, prev_version='3', prev_release='4',
            curr_epoch=0, curr_version='4', curr_release='5',
        ))
        self.db.commit()
        reply = self.client.get(
            'affected-by/foo'+
            '?collection=f25' +
            '&epoch1=0' +
            '&version1=1.2' +
            '&release1=3' +
            '&epoch2=0' +
            '&version2=4.5' +
            '&release2=6'
        )
        self.assertEqual(200, reply.status_code)
        text = reply.data.decode('utf-8')
        self.assertIn("bar", text)
        self.assertNotIn("baz", text)
        self.assertNotIn("xyzzy", text)
        self.assertIn("abc", text)
        self.assertIn("klm", text)
        self.assertIn("ijk", text)
        self.assertIn("pqr", text)
