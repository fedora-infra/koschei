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
# Author: Michael Simacek <msimacek@redhat.com>

from __future__ import print_function, absolute_import

import requests

from koschei.config import get_config
from koschei.plugin import listen_event


def query_pagure(session, url):
    baseurl = get_config('pagure.api_url')
    req = requests.get(baseurl + '/' + url)
    if not req.ok:
        session.log.info("pagure query failed %s, status=%d",
                         url, req.status_code)
        return None
    return req.json()


def query_users_packages(session, username):
    session.log.debug("Requesting pagure packages for {}".format(username))
    user = query_pagure(session, 'user/{}'.format(username))
    if not user:
        return None
    return [repo['name'] for repo in user['repos'] if repo['namespace'] == 'rpms']


@listen_event('get_user_packages')
def get_user_packages(session, username):
    def create():
        names = query_users_packages(session, username)
        return names

    return session.cache('pagure.users').get_or_create(str(username), create)
