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

"""
Module defining page navigation tabs.
"""

from functools import wraps

from flask import g, url_for

from koschei.frontend.base import app

tabs = []


class Tab(object):
    """
    A single tab of main page navigation. Used as a decorator on view functions to make
    them visible/available under given tab.

    If an endpoint is rendered, the associated tab is marked as active.
    If an endpoint is marked as master (using the master method to get the decorator),
    the tab will link to that endpoint.
    """
    def __init__(self, name, order=0, requires_user=False):
        """
        :param name: Tab name
        :param order: Tabs are ordered by this
        :param requires_user: Whether the functionality needs the user to be logged in.
                              Will be placed in the user menu instead. The view function
                              must take 'username' parameter.
        """
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
        """
        Decorate given function to be visible under given tab.
        """
        @wraps(fn)
        def decorated(*args, **kwargs):
            g.current_tab = self
            return fn(*args, **kwargs)
        return decorated

    def master(self, fn):
        """
        Return a decorator that marks the tab as master. Master means that this view
        function will be the target of the tab link.
        """
        self.master_endpoint = fn
        return self(fn)

    @property
    def url(self):
        """
        Construct URL to the endpoint (master view) of this tab.
        """
        name = self.master_endpoint.__name__
        if self.requires_user:
            return url_for(name, username=g.user.name)
        return url_for(name)

    @staticmethod
    def get_tabs():
        """
        Get all tabs to be rendered in main navigation menu.
        Available in templates as `get_tabs` global function.
        """
        return [t for t in tabs if t.master_endpoint and not t.requires_user]

    @staticmethod
    def get_user_tabs():
        """
        Get all tabls to be rendered in the user menu.
        Available in templates as `get_user_tabs` global function.
        """
        return [t for t in tabs if t.master_endpoint and t.requires_user]


app.jinja_env.globals['get_tabs'] = Tab.get_tabs
app.jinja_env.globals['get_user_tabs'] = Tab.get_user_tabs
