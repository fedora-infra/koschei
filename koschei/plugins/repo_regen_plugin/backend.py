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

from __future__ import print_function, absolute_import

import logging

import koji

from koschei.backend import koji_util
from koschei.models import Collection, RepoMapping
from koschei.plugin import listen_event

log = logging.getLogger('koschei.plugin.repo_regen')


def ensure_tag(koji_session, tag_name):
    tag = koji_session.getTag(tag_name)
    if not tag:
        arches = ','.join(koji_session.config['build_arches']) or None
        koji_session.createTag(tag_name, arches=arches)


@listen_event('polling_event')
def poll_secondary_repo(session):
    log.info("Polling new external repo")
    db = session.db
    primary = session.koji('primary')
    secondary = session.koji('secondary')
    for collection in db.query(Collection).filter_by(secondary_mode=True):
        ensure_tag(primary, collection.build_tag)
        target = primary.getBuildTarget(collection.target)
        if not target:
            log.info("Creating new secondary build target")
            primary.createBuildTarget(collection.target, collection.build_tag,
                                      collection.build_tag)
            groups = secondary.getTagGroups(collection.build_tag)
            primary.multicall = True
            for group in groups:
                primary.groupListAdd(collection.build_tag, group['name'])
                for pkg in group['packagelist']:
                    primary.groupPackageListAdd(
                        collection.build_tag,
                        group['name'],
                        pkg['package'],
                        type=pkg['type'],
                        blocked=pkg['blocked'],
                    )
            primary.multiCall()

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
