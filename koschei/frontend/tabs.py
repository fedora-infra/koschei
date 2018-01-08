# Copyright (C) 2014-2016  Red Hat, Inc.
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

from functools import wraps

from flask import g, url_for

from koschei.frontend.base import app

tabs = []


class Tab(object):
    def __init__(self, name, order=0, requires_user=False):
        self.name = name
        self.order = order
        self.requires_user = requires_user
        self.master_endpoint = None
        for i, tab in enumerate(tabs):
            if tab.order > order:
                tabs.insert(i, self)
                break
        else:
            tabs.append(self)

    def __call__(self, fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            g.current_tab = self
            return fn(*args, **kwargs)
        return decorated

    def master(self, fn):
        self.master_endpoint = fn
        return self(fn)

    @property
    def url(self):
        name = self.master_endpoint.__name__
        if self.requires_user:
            return url_for(name, username=g.user.name)
        return url_for(name)

    @staticmethod
    def get_tabs():
        return [t for t in tabs if t.master_endpoint and not t.requires_user]

    @staticmethod
    def get_user_tabs():
        return [t for t in tabs if t.master_endpoint and t.requires_user]


app.jinja_env.globals['get_tabs'] = Tab.get_tabs
app.jinja_env.globals['get_user_tabs'] = Tab.get_user_tabs
