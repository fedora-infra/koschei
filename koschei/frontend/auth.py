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

import re
import functools
from flask import abort, flash, request, session, redirect, url_for, g

from koschei.config import get_config
from koschei.frontend import app, db
import koschei.models as m

bypass_login = get_config('bypass_login', None)
user_re = get_config('frontend.auth.user_re')
user_re = re.compile('^{}$'.format(user_re))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if bypass_login:
        identity = "none"
        user_name = bypass_login
    else:
        identity = request.environ.get('REMOTE_USER') or abort(501)
        user_name = re.match(user_re, identity).group(1)
    user = db.query(m.User).filter_by(name=user_name).first()
    if not user:
        user = m.User(name=user_name, admin=bool(bypass_login))
        db.add(user)
        db.commit()
        flash('New user "{}" was registered.'.format(user_name))
    session['user'] = user_name
    flash('Logged in as user "{}" with identity "{}".'
          .format(user_name, identity))
    if user.admin:
        flash('You have admin privileges.')
    next_url = request.values.get("next", url_for('frontpage'))
    return redirect(next_url)


@app.before_request
def lookup_current_user():
    g.user = None
    user_name = session.get('user', None)
    if user_name:
        g.user = db.query(m.User).filter_by(name=user_name).one()


@app.route('/logout')
def logout():
    if session.pop('user', None):
        flash('Successfully logged out.')
    else:
        flash('You were not logged in.')
    return redirect(url_for('frontpage'))


def login_required():
    def decorator(func):
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            if g.user is None:
                return redirect(url_for('login', next=request.url))
            return func(*args, **kwargs)
        return decorated
    return decorator
