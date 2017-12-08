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

from koschei.frontend import app
from koschei.frontend.base import db
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
        self.user = self.prepare_user(name='jdoe', admin=False)
        login(self.client, self.user)
        return fn(*args, **kwargs)
    return decorated


def authenticate_admin(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        self = args[0]
        self.user = self.prepare_user(name='admin', admin=True)
        login(self.client, self.user)
        return fn(*args, **kwargs)
    return decorated


class FrontendTest(DBTest):
    def __init__(self, *args, **kwargs):
        super(FrontendTest, self).__init__(*args, **kwargs)
        self.user = None

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

        def rollback_session(exception=None):
            db.rollback()
        app.teardown_appcontext_funcs = [rollback_session]

    def tearDown(self):
        app.teardown_appcontext_funcs = self.teardown_appcontext_funcs
        app.do_teardown_appcontext()
        super(FrontendTest, self).tearDown()
