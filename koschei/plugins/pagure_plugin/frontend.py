# Copyright (C) 2017-2019 Red Hat, Inc.
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

import requests

from koschei.config import get_config
from koschei.plugin import listen_event


def get_packages_per_user(session):
    session.log.debug("Requesting pagure_owner_alias.json")
    req = requests.get(get_config('pagure.owner_alias_url'))
    if not req.ok:
        session.log.info("Failed to get pagure_owner_alias.json, status=%d",
                         req.status_code)
        return {}
    pkgs_per_user = {}
    for pkg, users in req.json()['rpms'].items():
        for user in users:
            pkgs_per_user.setdefault(user, []).append(pkg)
    return pkgs_per_user


@listen_event('get_user_packages')
def get_user_packages(session, username):
    def create():
        return get_packages_per_user(session)
    pkg_map = session.cache('pagure.users').get_or_create('packages_per_user', create)
    return pkg_map.get(str(username))
