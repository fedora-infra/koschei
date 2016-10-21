# Copyright (C) 2015-2016  Red Hat, Inc.
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

from . import query_pkgdb
from koschei.plugin import listen_event


def query_users_packages(session, username):
    session.log.debug("Requesting pkgdb packages for {}".format(username))
    packages = query_pkgdb(session, 'packager/package/{}'.format(username))
    if packages:
        packages = (packages['point of contact'] +
                    packages['co-maintained'] +
                    packages['watch'])
        return {p['name'] for p in packages}


@listen_event('get_user_packages')
def get_user_packages(session, username):
    def create():
        names = query_users_packages(session, username)
        return names

    return session.cache('plugin.pkgdb.users').get_or_create(str(username), create)
