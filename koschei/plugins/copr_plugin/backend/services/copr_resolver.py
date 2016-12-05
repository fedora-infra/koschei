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

from __future__ import print_function, absolute_import, division

import re
import os
import hawkey
from itertools import izip
from collections import defaultdict
from functools import cmp_to_key

from sqlalchemy.sql import insert
from copr.exceptions import CoprRequestException

from koschei import util
from koschei.models import (Package, CoprRebuildRequest, CoprRebuild,
                            CoprResolutionChange)
from koschei.config import get_config
from koschei.backend import depsolve, koji_util, repo_util
from koschei.backend.service import Service
from koschei.backend.repo_util import KojiRepoDescriptor

from copr_plugin import copr_client


class RequestProcessingError(Exception):
    pass


class CoprRepoDescriptor(object):
    def __init__(self, repo_id, url):
        self.repo_id = repo_id
        self.url = url

    def __str__(self):
        return self.repo_id


class CoprResolver(Service):
    def __init__(self, session):
        super(CoprResolver, self).__init__(session)

    def set_source_repo_url(self, request):
        owner, name = re.match(r'^copr:([^/]+)/([^/]+)$', request.repo_source).groups()
        try:
            project = copr_client.get_project_details(name, username=owner).data
        except CoprRequestException as e:
            raise RequestProcessingError("Couldn't obtain copr project: " + str(e))

        # TODO make customizable or at least configurable
        input_chroots = 'fedora-rawhide-x86_64', 'fedora-26-x86_64'
        for chroot in input_chroots:
            request.yum_repo = project['detail']['yum_repos'].get(chroot)
            if request.yum_repo:
                break
        else:
            raise RequestProcessingError("Input project doesn't have suitable chroot. "
                                         "Needs one of: " + ','.join(input_chroots))

    def add_repo_to_sack(self, request, sack):
        desc = CoprRepoDescriptor('copr-request-{}'.format(request.id), request.yum_repo)
        repo_dir = os.path.join(get_config('directories.cachedir'), 'user_repos')
        repo = repo_util.get_repo(repo_dir, desc, download=True)
        if not repo:
            raise RequestProcessingError("Cannot download user repo")
        sack.load_yum_repo(repo, load_filelists=True)
        if get_config('copr.overriding_by_exclusions', False):
            exclusions = []
            pkg_by_name = defaultdict(list)
            for pkg in hawkey.Query(sack):
                pkg_by_name[pkg.name].append(pkg)

            def pkg_cmp(pkg1, pkg2):
                return util.compare_evr(
                    (pkg1.epoch, pkg1.version, pkg1.release),
                    (pkg2.epoch, pkg2.version, pkg2.release),
                )

            for pkgs in pkg_by_name.values():
                if len(pkgs) > 1:
                    # TODO there are duplicated packages in base repo for unknown reason
                    exclusions += sorted(pkgs, key=cmp_to_key(pkg_cmp))[:-1]

            sack.add_excludes(exclusions)

    def resolve_request(self, request, sack_before, sack_after):
        self.log.info("Processing rebuild request id {}".format(request.id))

        # packages with no build have no srpm to fetch buildrequires, so filter them
        packages = self.db.query(Package)\
            .filter_by(tracked=True, blocked=False)\
            .filter(Package.last_complete_build_state != None)\
            .order_by(Package.id)\
            .all()

        br_gen = koji_util.get_rpm_requires_cached(
            self.session,
            self.session.secondary_koji_for(request.collection),
            [p.srpm_nvra for p in packages]
        )
        rebuilds = []
        resolution_changes = []
        build_group = koji_util.get_build_group_cached(
            self.session,
            self.session.koji('primary'),
            request.collection.build_tag,
            request.collection.build_group,
        )
        for package, brs in izip(packages, br_gen):
            resolved1, _, installs1 = \
                depsolve.run_goal(sack_before, brs, build_group)
            resolved2, problems2, installs2 = \
                depsolve.run_goal(sack_after, brs, build_group)
            if resolved1 != resolved2:
                change = dict(
                    request_id=request.id,
                    package_id=package.id,
                    prev_resolved=resolved1,
                    curr_resolved=resolved2,
                    problems=problems2,
                    # TODO compare problems as well?
                )
                resolution_changes.append(change)
            elif resolved2:
                installs1 = set(installs1)
                installs2 = set(installs2)
                if installs1 != installs2:
                    changed_deps = [
                        depsolve.DependencyWithDistance(
                            name=pkg.name, epoch=pkg.epoch,
                            version=pkg.version, release=pkg.release,
                            arch=pkg.arch,
                        ) for pkg in installs2 if pkg not in installs1
                    ]
                    depsolve.compute_dependency_distances(sack_after, brs, changed_deps)
                    priority = sum(100 / (d.distance * 2)
                                   for d in changed_deps if d.distance)
                    rebuild = dict(
                        request_id=request.id,
                        package_id=package.id,
                        prev_state=package.last_complete_build_state,
                        priority=priority,
                    )
                    rebuilds.append(rebuild)
        if rebuilds:
            for i, rebuild in enumerate(sorted(rebuilds, key=lambda x: -x['priority'])):
                del rebuild['priority']
                rebuild['order'] = i
            self.db.execute(insert(CoprRebuild, rebuilds))
        if resolution_changes:
            self.db.execute(insert(CoprResolutionChange, resolution_changes))

        if rebuilds or resolution_changes:
            request.state = 'in progress'
        else:
            request.state = 'finished'
        self.log.info("Rebuild request id {} processed".format(request.id))

    def main(self):
        requests = self.db.query(CoprRebuildRequest)\
            .filter_by(state='new')\
            .all()
        # TODO group by collection?
        for request in requests:
            try:
                collection = request.collection
                request.repo_id = collection.latest_repo_id
                repo_descriptor = KojiRepoDescriptor(
                    koji_id='secondary' if collection.secondary_mode else 'primary',
                    repo_id=request.repo_id,
                    build_tag=collection.build_tag,
                )
                self.set_source_repo_url(request)
                sack_before = self.session.repo_cache.get_sack(repo_descriptor)
                if not sack_before:
                    raise RuntimeError("Couldn't download koji repo")
                sack_after = self.session.repo_cache.get_sack(repo_descriptor)
                self.add_repo_to_sack(request, sack_after)
                self.resolve_request(request, sack_before, sack_after)
                self.db.commit()
            except RequestProcessingError as e:
                request.state = 'failed'
                request.error = str(e)
                self.db.commit()
