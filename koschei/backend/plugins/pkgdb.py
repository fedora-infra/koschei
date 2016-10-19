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

import re
import logging
import requests
import fedmsg.meta
import dogpile.cache

from koschei import backend
from koschei.models import Package
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


# TODO share this with frontend plugin
def query_pkgdb(url):
    baseurl = get_config('pkgdb.pkgdb_url')
    req = requests.get(baseurl + '/' + url)
    if req.status_code != 200:
        log.info("pkgdb query failed %s, status=%d",
                 url, req.status_code)
        return None
    return req.json()


def query_monitored_packages():
    log.debug("Requesting list of monitored packages from pkgdb")
    packages = query_pkgdb('koschei?format=json')
    if packages:
        return packages['packages']


@listen_event('fedmsg_event')
def consume_fedmsg(session, topic, msg):
    topic_re = re.compile(get_config('pkgdb.topic_re'))
    if topic_re.search(topic):
        if topic.endswith('.pkgdb.package.koschei.update'):
            package = msg['msg']['package']
            name = package['name']
            tracked = package['koschei_monitor']
            log.debug('Setting tracking flag for package %s to %r',
                      name, tracked)
            session.db.query(Package)\
                .filter_by(name=name)\
                .update({'tracked': tracked}, synchronize_session=False)
            session.db.expire_all()
            session.db.commit()
        for username in fedmsg.meta.msg2usernames(msg):
            get_cache().delete(str(username))


@listen_event('polling_event')
def refresh_monitored_packages(session):
    try:
        if get_config('pkgdb.sync_tracked'):
            log.debug('Polling monitored packages...')
            packages = query_monitored_packages()
            if packages is not None:
                backend.sync_tracked(session, packages)
    except requests.ConnectionError:
        log.exception("Polling monitored packages failed, skipping cycle")
