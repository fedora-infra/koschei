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

import logging
import requests
import dogpile.cache

from koschei.config import get_config
from koschei.plugin import listen_event

log = logging.getLogger('koschei.plugin.pkgdb')

__cache = None


def get_cache():
    global __cache
    if __cache:
        return __cache
    __cache = dogpile.cache.make_region()
    __cache.configure(**get_config('pkgdb.cache'))
    return __cache


# TODO share this with backend plugin
def query_pkgdb(url):
    baseurl = get_config('pkgdb.pkgdb_url')
    req = requests.get(baseurl + '/' + url)
    if req.status_code != 200:
        log.info("pkgdb query failed %s, status=%d",
                 url, req.status_code)
        return None
    return req.json()


def query_users_packages(username):
    log.debug("Requesting pkgdb packages for {}".format(username))
    packages = query_pkgdb('packager/package/{}'.format(username))
    if packages:
        packages = (packages['point of contact'] +
                    packages['co-maintained'] +
                    packages['watch'])
        return {p['name'] for p in packages}


@listen_event('get_user_packages')
def get_user_packages(username):
    def create():
        names = query_users_packages(username)
        return names

    return get_cache().get_or_create(str(username), create)
