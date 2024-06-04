# Copyright (C) 2014-2016  Red Hat, Inc.
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

import shutil

from copr.v3.exceptions import CoprException
from sqlalchemy import literal_column

from koschei.config import get_config
from koschei.plugin import listen_event
from koschei.models import CoprRebuild, CoprRebuildRequest

from .common import copr_client, get_request_cachedir


@listen_event('cleanup')
def copr_cleanup(session, older_than):
    session.log.debug('Cleaning up old copr projects')
    interval = "now() - '{} month'::interval".format(older_than)
    to_delete_ids = session.db.query(CoprRebuildRequest.id)\
        .filter(CoprRebuildRequest.timestamp < literal_column(interval))\
        .all_flat()
    if to_delete_ids:
        rebuilds = session.db.query(CoprRebuild)\
            .filter(CoprRebuild.request_id.in_(to_delete_ids))\
            .filter(CoprRebuild.copr_build_id != None)\
            .all()
        for rebuild in rebuilds:
            try:
                copr_client.project_proxy.delete_project(
                    ownername=get_config('copr.copr_owner'),
                    projectname=rebuild.copr_name,
                )
            except CoprException as e:
                if 'does not exist' not in str(e):
                    session.log.warn("Cannot delete copr project {}: {}"
                                     .format(rebuild.copr_name, e))
                    if rebuild.request_id in to_delete_ids:
                        to_delete_ids.remove(rebuild.request_id)
    if to_delete_ids:
        session.db.query(CoprRebuildRequest)\
            .filter(CoprRebuildRequest.id.in_(to_delete_ids))\
            .delete()
        for request_id in to_delete_ids:
            shutil.rmtree(get_request_cachedir(request_id), ignore_errors=True)
        session.log_user_action(
            "Cleanup: Deleted {} copr requests"
            .format(len(to_delete_ids))
        )
