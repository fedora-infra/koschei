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

import logging
import flask
import functools
from flask_openid import OpenID

from koschei.config import get_config
from koschei.models import User, get_or_create
from koschei.frontend import app, db


provider = get_config('openid.openid_provider')
openid = OpenID(app, get_config('openid.openid_store'), safe_roots=[])


class TypeURIMismatchFilter(logging.Filter):
    def filter(self, record):
        return 'TypeURIMismatch' not in record.getMessage()


logging.getLogger().addFilter(TypeURIMismatchFilter())


def username_to_openid(name):
    return "http://{}.{}/".format(name, provider)


def openid_to_username(oid):
    return oid.replace(".{}/".format(provider), "")\
              .replace("http://", "")


@app.route("/login", methods=["GET", "POST"])
@openid.loginhandler
def login():
    if flask.g.user is not None:
        return flask.redirect(openid.get_next_url())
    else:
        return openid.try_login("https://{}/".format(provider),
                                ask_for=["email", "timezone"])


@openid.after_login
def create_or_login(response):
    flask.session["openid"] = response.identity_url
    username = openid_to_username(response.identity_url)
    user = get_or_create(db, User, name=username)
    user.email = response.email
    user.timezone = response.timezone
    db.commit()
    flask.g.user = user

    return flask.redirect(openid.get_next_url())


@app.before_request
def lookup_current_user():
    flask.g.user = None
    if "openid" in flask.session:
        username = openid_to_username(flask.session["openid"])
        flask.g.user = db.query(User).filter_by(name=username).first()
    if get_config('bypass_login'):
        flask.g.user = get_or_create(db, User, name=get_config('bypass_login'))


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
