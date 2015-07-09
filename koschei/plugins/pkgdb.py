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


def query_pkgdb(url):
    baseurl = pkgdb_config['pkgdb_url']
    req = requests.get(baseurl + '/' + url)
    if req.status_code != 200:
        log.info("pkgdb query failed {}, status={}"
                 .format(url, req.status_code))
        return None
    return req.json()

def query_users_packages(username):
    log.debug("Requesting pkgdb packages for " + username)
    packages = query_pkgdb('packager/package/' + username)
    if packages:
        packages = packages['point of contact'] + packages['co-maintained'] + packages['watch']
        return {p['name'] for p in packages}


if pkgdb_config['enabled']:

    topic_re = re.compile(pkgdb_config['topic_re'])


    @listen_event('refresh_user_packages')
    def refresh_user_packages(user):
        if not user.packages_retrieved:
            db = Session.object_session(user)
            names = query_users_packages(user.name)
            if names is not None:
                user.packages_retrieved = True
                existing = {p for [p] in db.query(Package.name).filter(Package.name.in_(names)).all()}
                for name in names:
                    if name not in existing:
                        pkg = Package(name=name, tracked=False)
                        db.add(pkg)
                db.flush()
                packages = db.query(Package.id).filter(Package.name.in_(names)).all()
                entries = [{'user_id': user.id, 'package_id': pkg.id} for pkg in packages]
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
