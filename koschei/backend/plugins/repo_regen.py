# Copyright (C) 2014-2016 Red Hat, Inc.
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

import koji

from koschei.backend import koji_util
from koschei.models import Collection, RepoMapping
from koschei.plugin import listen_event

log = logging.getLogger('koschei.repo_regen_plugin')


@listen_event('polling_event')
def poll_secondary_repo(backend):
    log.debug("Polling new external repo")
    db = backend.db
    primary = backend.koji_sessions['primary']
    secondary = backend.koji_sessions['secondary']
    for collection in db.query(Collection).filter_by(secondary_mode=True).all():
        remote_repo = koji_util.get_latest_repo(secondary, collection.build_tag)
        mapping = db.query(RepoMapping)\
            .filter_by(secondary_id=remote_repo['id'])\
            .first()
        if not mapping:
            log.info("Requesting new repo for %s", collection)
            try:
                repo_url = secondary.config['repo_url']\
                    .format(build_tag=collection.build_tag,
                            repo_id=remote_repo['id'],
                            arch="$arch")
                tag = collection.build_tag
                for repo in primary.getTagExternalRepos(tag):
                    primary.removeExternalRepoFromTag(tag, repo['external_repo_id'])
                name = '{}-{}'.format(collection.name, remote_repo['id'])
                repo = (primary.getExternalRepo(name) or
                        primary.createExternalRepo(name, repo_url))
                primary.addExternalRepoToTag(tag, repo['name'], 0)  # priority
                task_id = primary.newRepo(tag)
                db.add(RepoMapping(task_id=task_id,
                                   secondary_id=remote_repo['id']))
                db.commit()
            except koji.GenericError:
                log.exception("Requesting new repo failed")
