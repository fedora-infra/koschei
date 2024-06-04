# Copyright (C) 2016  Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import os

from sqlalchemy.sql.expression import func
from copr.v3.exceptions import CoprNoResultException
from textwrap import dedent

from koschei.models import Build, CoprRebuildRequest, CoprRebuild
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.service import Service
from koschei.backend.koji_util import KojiRepoDescriptor

from koschei.plugins.copr_plugin.backend.common import (
    copr_client, get_request_comps_path, repo_descriptor_for_request, prepare_comps
)


class CoprScheduler(Service):
    def __init__(self, session):
        super(CoprScheduler, self).__init__(session)
        self.copr_owner = get_config('copr.copr_owner')
        self.chroot_name = get_config('copr.chroot_name')
        self.description_template = dedent(get_config('copr.description')).strip()
        self.instructions_template = dedent(get_config('copr.instructions')).strip()

    def get_koji_id(self, collection):
        return 'secondary' if collection.secondary_mode else 'primary'

    def create_copr_project(self, request, copr_name):
        koji_repo = KojiRepoDescriptor(self.get_koji_id(request.collection),
                                       request.collection.build_tag,
                                       request.repo_id)

        params = {'request_id': request.id}
        description = self.description_template.format(**params)
        instructions = self.instructions_template.format(**params)

        try:
            # there may be leftover project from crashed process
            copr_client.project_proxy.delete(
                projectname=copr_name,
                ownername=self.copr_owner,
            )
        except CoprNoResultException:
            pass

        copr_client.project_proxy.add(
            projectname=copr_name,
            ownername=self.copr_owner,
            chroots=[self.chroot_name],
            devel_mode=False,
            enable_net=False,
            additional_repos=[request.yum_repo, koji_repo.url],
            unlisted_on_hp=True,
            description=description,
            instructions=instructions,
        )

        comps = get_request_comps_path(request)
        if not os.path.isfile(comps):
            # this may download and load whole repo, not just comps, but it
            # should only happen after we lose storage, such as when respawning
            # the machine, so it's not worth optimizing
            repo_descriptor = repo_descriptor_for_request(request)
            self.session.repo_cache.get_sack(repo_descriptor)
            prepare_comps(self.session, request, repo_descriptor)

        copr_client.project_chroot_proxy.edit(
            projectname=copr_name,
            ownername=self.copr_owner,
            chrootname=self.chroot_name,
            comps=comps,
            additional_packages=['@' + request.collection.build_group],
        )
        self.log.debug("Created copr project " + copr_name)

    def schedule_rebuild(self, rebuild):
        koji_session = self.session.koji(self.get_koji_id(rebuild.package.collection))
        srpm_url = koji_util.get_last_srpm(koji_session,
                                           rebuild.package.collection.dest_tag,
                                           rebuild.package.name)[1]
        self.create_copr_project(rebuild.request, rebuild.copr_name)
        copr_build = copr_client.build_proxy.create_from_url(
            projectname=rebuild.copr_name,
            ownername=self.copr_owner,
            url=srpm_url,
            buildopts={
                'background': True,
            },
        )
        rebuild.copr_build_id = copr_build['ids'][0]
        rebuild.state = Build.RUNNING
        self.db.commit()
        self.log.info("Build {} scheduled". format(rebuild.copr_build_id))

    def main(self):
        running_builds = self.db.query(CoprRebuild)\
            .filter(CoprRebuild.state == Build.RUNNING)\
            .count()
        if running_builds >= get_config('copr.max_builds'):
            self.log.debug("{} running builds, not scheduling".format(running_builds))
            return

        last_index = self.db.query(
            func.coalesce(
                func.max(CoprRebuildRequest.scheduler_queue_index),
                0
            )
        ).scalar() + 1
        request = self.db.query(CoprRebuildRequest)\
            .order_by(CoprRebuildRequest.scheduler_queue_index.nullsfirst())\
            .filter(CoprRebuildRequest.state == 'in progress')\
            .first()
        if not request:
            self.log.debug("No schedulable requests")
            return

        # move all requests of given user to the queue end
        self.db.query(CoprRebuildRequest)\
            .filter(CoprRebuildRequest.user_id == request.user_id)\
            .update({'scheduler_queue_index': last_index})

        build_count = self.db.query(CoprRebuild)\
            .filter(CoprRebuild.request_id == request.id)\
            .filter(CoprRebuild.copr_build_id != None)\
            .count()
        if build_count >= request.schedule_count:
            request.state = 'scheduled'
            self.db.commit()
            return

        rebuild = self.db.query(CoprRebuild)\
            .filter(CoprRebuild.copr_build_id == None)\
            .order_by(CoprRebuild.order)\
            .first()

        if not rebuild:
            request.state = 'scheduled'
            self.db.commit()
        else:
            self.schedule_rebuild(rebuild)
