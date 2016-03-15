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
import dogpile.cache

from koschei.models import Package, Collection
from koschei.util import config
from koschei.plugin import listen_event

log = logging.getLogger('koschei.pkgdb_plugin')

pkgdb_config = config['pkgdb']

user_cache = dogpile.cache.make_region()
user_cache.configure(**pkgdb_config['cache'])


def query_pkgdb(url):
    baseurl = pkgdb_config['pkgdb_url']
    req = requests.get(baseurl + '/' + url)
    if req.status_code != 200:
        log.info("pkgdb query failed %s, status=%d",
                 url, req.status_code)
        return None
    return req.json()


def query_users_packages(username, branch):
    log.debug("Requesting pkgdb packages for {} (branch {})"
              .format(username, branch))
    packages = query_pkgdb('packager/package/{}?branches={}'
                           .format(username, branch))
    if packages:
        packages = (packages['point of contact'] +
                    packages['co-maintained'] +
                    packages['watch'])
        return {p['name'] for p in packages}


def query_monitored_packages():
    log.debug("Requesting list of monitored packages from pkgdb")
    packages = query_pkgdb('koschei?format=json')
    if packages:
        return packages['packages']

def user_key(collection, username):
    return "{}###{}".format(collection.id, username)


if pkgdb_config['enabled']:

    topic_re = re.compile(pkgdb_config['topic_re'])

    @listen_event('get_user_packages')
    def get_user_packages(db, username, current_collection):
        def create():
            names = query_users_packages(username, current_collection.branch)
            if names:
                return db.query(Package.id)\
                    .filter(Package.name.in_(names))\
                    .filter(Package.collection_id == current_collection.id)\
                    .all_flat()

        return user_cache.get_or_create(user_key(current_collection, username), create)

    @listen_event('fedmsg_event')
    def consume_fedmsg(topic, msg, db, **kwargs):
        if topic_re.search(topic):
            if topic.endswith('.pkgdb.package.koschei.update'):
                package = msg['msg']['package']
                name = package['name']
                tracked = package['koschei_monitor']
                log.debug('Setting tracking flag for package %s to %r',
                          name, tracked)
                db.query(Package)\
                  .filter_by(name=name)\
                  .update({'tracked': tracked}, synchronize_session=False)
                db.expire_all()
                db.commit()
            collection_ids = db.query(Collection.id).all_flat()
            for username in fedmsg.meta.msg2usernames(msg):
                for collection_id in collection_ids:
                    user_cache.delete(user_key(collection_id, username))

    @listen_event('polling_event')
    def refresh_monitored_packages(backend):
        try:
            if pkgdb_config['sync_tracked']:
                log.debug('Polling monitored packages...')
                packages = query_monitored_packages()
                if packages is not None:
                    backend.sync_tracked(packages)
        except requests.ConnectionError:
            log.exception("Polling monitored packages failed, skipping cycle")
