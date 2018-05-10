# Copyright (C) 2014-2016 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

"""
Module handling authentication.
Koschei delegates authentication to the environment (Apache httpd) and uses environment
variables to get the logged in users.
The login endpoint must be guarded by httpd's authentication.
"""

import functools
import re

from flask import abort, request, session, redirect, url_for, g

import koschei.models as m
from koschei.config import get_config
from koschei.frontend.base import app, db
from koschei.frontend.util import flash_info, flash_ack

bypass_login = get_config('bypass_login', None)
user_re = get_config('frontend.auth.user_re')
user_env = get_config('frontend.auth.user_env')
user_re = re.compile('^{}$'.format(user_re))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Acknowledge the logged in user by adding it's name to session.
    The login itself must have had already happened in httpd.
    Adds the user to the database on first login.
    """
    if bypass_login:
        identity = "none"
        user_name = bypass_login
    else:
        identity = request.environ.get(user_env) or abort(501)
        user_name = re.match(user_re, identity).group(1)
    user = db.query(m.User).filter_by(name=user_name).first()
    if not user:
        user = m.User(name=user_name, admin=bool(bypass_login))
        db.add(user)
        db.commit()
        flash_info('New user "{}" was registered.'.format(user_name))
    session['user'] = user_name
    flash_ack('Logged in as user "{}" with identity "{}".'
              .format(user_name, identity))
    if user.admin:
        flash_info('You have admin privileges.')
    next_url = request.values.get("next", url_for('frontpage'))
    return redirect(next_url)


@app.before_request
def lookup_current_user():
    """
    If logged in, bind the current user to g.user
    """
    if request.endpoint == 'static':
        return
    g.user = None
    user_name = session.get('user', None)
    if user_name:
        g.user = db.query(m.User).filter_by(name=user_name).first()


@app.route('/logout')
def logout():
    """
    Remove the user from session cookie. It doesn't imply logging out of a federated login
    provider.
    """
    if session.pop('user', None):
        flash_ack('Successfully logged out.')
    else:
        flash_info('You were not logged in.')
    return redirect(url_for('frontpage'))


def login_required():
    """
    Decorates endpoint function that requires logged in user. If such endpoint is accessed
    by non-logged user, it redirects him to login URL.
    :return:
    """
    def decorator(func):
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            if g.user is None:
                return redirect(url_for('login', next=request.url))
            return func(*args, **kwargs)
        return decorated
    return decorator
