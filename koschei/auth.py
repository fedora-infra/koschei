# Copyright (C) 2014  Red Hat, Inc.
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

import flask
import functools
from flask.ext.openid import OpenID

from koschei.util import config
from koschei.models import User
from koschei.frontend import app, db_session

openid = OpenID(app, config['openid']['openid_store'], safe_roots=[])

def username_to_openid(name):
    return "http://{}.id.fedoraproject.org/".format(name)

def openid_to_username(oid):
    return oid.replace(".id.fedoraproject.org/", "")\
              .replace("http://", "")

@app.route("/login", methods=["GET"])
@openid.loginhandler
def login():
    if flask.g.user is not None:
        return flask.redirect(openid.get_next_url())
    else:
        return openid.try_login("https://id.fedoraproject.org/",
                                ask_for=["email", "timezone"])

@openid.after_login
def create_or_login(response):
    flask.session["openid"] = response.identity_url
    username = openid_to_username(response.identity_url)
    user = db_session.query(User).filter_by(name=username).first()
    if not user:
        user = User(name=username)
        db_session.add(user)
    user.email = response.email
    user.timezone = response.timezone
    db_session.commit()
    flask.g.user = user

    return flask.redirect(openid.get_next_url())

@app.before_request
def lookup_current_user():
    flask.g.user = None
    if "openid" in flask.session:
        username = openid_to_username(flask.session["openid"])
        flask.g.user = db_session.query(User).filter_by(name=username).first()

@app.route("/logout")
def logout():
    flask.session.pop("openid", None)
    return flask.redirect(openid.get_next_url())

def login_required():
    def decorator(func):
        @functools.wraps(func)
        def decorated(*args, **kwargs):
            if flask.g.user is None:
                return flask.redirect(flask.url_for("login",
                                                    next=flask.request.url))

            return func(*args, **kwargs)
        return decorated
    return decorator
