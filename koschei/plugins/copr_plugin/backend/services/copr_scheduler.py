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

from sqlalchemy.sql.expression import func
from copr.exceptions import CoprRequestException

from koschei.models import Build, CoprRebuildRequest, CoprRebuild
from koschei.config import get_config, get_koji_config
from koschei.backend import koji_util
from koschei.backend.service import Service
from koschei.backend.repo_util import KojiRepoDescriptor

# pylint:disable=import-error
from copr_plugin import copr_client


class CoprScheduler(Service):
    def __init__(self, session):
        super(CoprScheduler, self).__init__(session)
        self.copr_owner = get_config('copr.copr_owner')
        self.chroot_name = get_config('copr.chroot_name')

    def get_koji_id(self, collection):
        return 'secondary' if collection.secondary_mode else 'primary'

    def get_topurl(self, collection):
        return get_koji_config(self.get_koji_id(collection), 'topurl')

    def get_rpm_path(self, nvra):
        path = '{name}/{version}/{release}/{arch}/{name}-{version}-{release}.{arch}.rpm'
        return path.format(**nvra)

    def get_srpm_url(self, package):
        """
        Returns last SRPM on Koji for given package,
        or None if last complete build is unknown.
        """
        srpm_nvra = package.srpm_nvra
        srpm_url = '{topurl}/packages/{path}'
        return srpm_url.format(topurl=self.get_topurl(package.collection),
                               path=self.get_rpm_path(srpm_nvra))

    def get_comps_url(self, collection, repo_id):
        url = '{topurl}/repos/{build_tag}/{repo_id}/groups/comps.xml'
        return url.format(topurl=self.get_topurl(collection),
                          build_tag=collection.build_tag,
                          repo_id=repo_id)

    def get_chroot_packages(self, collection):
        return koji_util.get_build_group_cached(
            self.session,
            self.session.koji('primary'),
            collection.build_tag,
            collection.build_group,
        )

    def create_copr_project(self, request, copr_name):
        koji_repo = KojiRepoDescriptor(self.get_koji_id(request.collection),
                                       request.collection.build_tag,
                                       request.repo_id)

        try:
            # there may be leftover project from crashed process
            copr_client.delete_project(
                projectname=copr_name,
                username=self.copr_owner,
            )
        except CoprRequestException:
            pass

        copr_client.create_project(
            projectname=copr_name,
            username=self.copr_owner,
            chroots=[self.chroot_name],
            disable_createrepo=True,
            enable_net=False,
            repos=[request.yum_repo, koji_repo.url],
            unlisted_on_hp=True,
        )

        copr_client.modify_project_chroot_details(
            projectname=copr_name,
            username=self.copr_owner,
            chrootname=self.chroot_name,
            pkgs=self.get_chroot_packages(request.collection),
        )
        self.log.debug("Created copr project " + copr_name)

    def schedule_rebuild(self, rebuild):
        srpm_url = self.get_srpm_url(rebuild.package)
        self.create_copr_project(rebuild.request, rebuild.copr_name)
        copr_build = copr_client.create_new_build(
            projectname=rebuild.copr_name,
            username=self.copr_owner,
            pkgs=[srpm_url],
            background=True,
        ).data
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
