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

from __future__ import print_function, absolute_import

import re
import requests
import fedmsg.meta

from . import query_pkgdb

from koschei import backend
from koschei.models import Package
from koschei.config import get_config
from koschei.plugin import listen_event


def query_monitored_packages(session):
    session.log.debug("Requesting list of monitored packages from pkgdb")
    packages = query_pkgdb(session, 'koschei?format=json')
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
            session.log.debug('Setting tracking flag for package %s to %r',
                              name, tracked)
            session.db.query(Package)\
                .filter_by(name=name)\
                .update({'tracked': tracked}, synchronize_session=False)
            session.db.expire_all()
            session.db.commit()
        for username in fedmsg.meta.msg2usernames(msg):
            session.cache('plugin.pkgdb.users').delete(str(username))


@listen_event('polling_event')
def refresh_monitored_packages(session):
    try:
        if get_config('pkgdb.sync_tracked'):
            session.log.debug('Polling monitored packages...')
            packages = query_monitored_packages(session)
            if packages is not None:
                backend.sync_tracked(session, packages)
    except requests.ConnectionError:
        session.log.exception("Polling monitored packages failed, skipping cycle")
