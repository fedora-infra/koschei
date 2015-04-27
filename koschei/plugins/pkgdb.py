# Copyright (C) 2015  Red Hat, Inc.
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

from sqlalchemy.sql import delete, insert

from koschei.models import Session, User, UserPackageRelation, Package
from koschei.util import config
from koschei.plugin import listen_event

log = logging.getLogger('koschei.pkgdb_plugin')

pkgdb_config = config['pkgdb']


def query_users_packages(username):
    log.debug("Requesting pkgdb packages for " + username)

    baseurl = pkgdb_config['pkgdb_url']
    url = '{0}/packager/package/{1}'.format(baseurl, username)
    req = requests.get(url)
    if req.status_code != 200:
        log.info("Couldn't get pkgdb packages for {}, status={}"
                 .format(username, req.status_code))
        return []

    data = req.json()
    packages = data['point of contact'] + data['co-maintained'] + data['watch']
    return [p['name'] for p in packages]


if pkgdb_config['enabled']:

    topic_re = re.compile(pkgdb_config['topic_re'])


    @listen_event('refresh_user_packages')
    def refresh_user_packages(user):
        if not user.packages_retrieved:
            db = Session.object_session(user)
            packages = query_users_packages(user.name)
            user.packages_retrieved = True
            if packages:
                existing = db.query(Package.id).filter(Package.name.in_(packages)).all()
                entries = [{'user_id': user.id, 'package_id': pkg.id} for pkg in existing]
                db.execute(delete(UserPackageRelation, UserPackageRelation.user_id == user.id))
                db.execute(insert(UserPackageRelation, entries))
            db.commit()


    @listen_event('fedmsg_event')
    def consume_fedmsg(topic, msg, db, **kwargs):
        if topic_re.search(topic):
            for username in fedmsg.meta.msg2usernames(msg):
                user = db.query(User).filter_by(name=username).first()
                if user:
                    user.packages_retrieved = False
                    db.commit()
                    refresh_user_packages(user)
