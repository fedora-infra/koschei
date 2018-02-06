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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function, absolute_import

from itertools import groupby
from six.moves import zip as izip

from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from koschei import util
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.models import (
    Collection, Package, AppliedChange, Build,
)

from koschei.backend.services.resolver import Resolver


class BuildResolver(Resolver):
    def main(self):
        for collection in self.db.query(Collection).all():
            self.process_builds(collection)

    def process_builds(self, collection):
        # pylint: disable=E1101
        builds = self.db.query(Build.id, Build.repo_id, Build.real, Build.package_id,
                               Package.name, Build.version, Build.release, Build.started,
                               Package.last_build_id)\
            .join(Build.package)\
            .filter(Build.deps_resolved == None)\
            .filter(Build.repo_id != None)\
            .filter(Package.collection_id == collection.id)\
            .order_by(Build.repo_id).all()

        descriptors = [self.create_repo_descriptor(collection.secondary_mode,
                                                   build.repo_id)
                       for build in builds]
        self.set_descriptor_tags(collection, descriptors)
        builds_to_process = []
        repos_to_process = []
        unavailable_build_ids = []
        for descriptor, build in izip(descriptors, builds):
            if descriptor.build_tag:
                repos_to_process.append(descriptor)
                builds_to_process.append(build)
            else:
                unavailable_build_ids.append(build.id)
        if unavailable_build_ids:
            self.db.query(Build)\
                .filter(Build.id.in_(unavailable_build_ids))\
                .update({'deps_resolved': False})
            self.process_unresolved_builds(unavailable_build_ids)
            self.db.commit()
        buildrequires = koji_util.get_rpm_requires_cached(
            self.session,
            self.session.secondary_koji_for(collection),
            [dict(name=b.name, version=b.version, release=b.release, arch='src')
             for b in builds_to_process]
        )
        if len(builds) > 100:
            buildrequires = util.parallel_generator(buildrequires, queue_size=0)
        for repo_descriptor, group in groupby(izip(repos_to_process,
                                                   builds_to_process,
                                                   buildrequires),
                                              lambda item: item[0]):
            with self.session.repo_cache.get_sack(repo_descriptor) as sack:
                if sack:
                    for _, build, brs in group:
                        build_group = self.get_build_group(collection)
                        _, _, curr_deps = self.resolve_dependencies(
                            sack, brs, build_group)
                        try:
                            self.process_build(build, curr_deps)
                            self.db.commit()
                        except (StaleDataError, ObjectDeletedError):
                            # build deleted concurrently
                            self.db.rollback()
                else:
                    self.log.info("Repo id=%d not available, skipping",
                                  repo_descriptor.repo_id)
                del sack
        self.db.query(Build)\
            .filter_by(repo_id=None)\
            .filter(Build.state.in_(Build.FINISHED_STATES))\
            .update({'deps_resolved': False}, synchronize_session=False)
        self.db.commit()

    def process_unresolved_builds(self, build_ids):
        """
        This function bumps priority of packages that have given builds as
        last.
        Packages that have last build unresolved cannot be resolved and
        should be treated as new packages (which they most likely are),
        because they cannot get priority from dependencies.
        """
        priority_value = get_config('priorities.newly_added')
        self.db.query(Package)\
            .filter(Package.last_build_id.in_(build_ids))\
            .update({'build_priority': priority_value})

    def process_build(self, entry, curr_deps):
        self.log.info("Processing build {}".format(entry.id))
        prev = self.get_prev_build_for_comparison(entry)
        deps = self.store_deps(curr_deps)
        self.db.query(Build).filter_by(id=entry.id)\
            .update({'deps_resolved': curr_deps is not None,
                     'dependency_keys': [dep.id for dep in deps]})
        if curr_deps is None:
            self.process_unresolved_builds([entry.id])
            return
        if prev and prev.dependency_keys:
            prev_deps = self.dependency_cache.get_by_ids(self.db, prev.dependency_keys)
            if prev_deps and curr_deps:
                changes = self.create_dependency_changes(prev_deps, curr_deps,
                                                         build_id=entry.id)
                if changes:
                    self.db.execute(
                        AppliedChange.__table__.insert(),
                        list(map(self.change_to_applied, changes)),
                    )
        self.db.query(Build)\
            .filter_by(package_id=entry.package_id)\
            .filter(Build.repo_id < entry.repo_id)\
            .update({'dependency_keys': None})

    def store_deps(self, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = (install.name, install.epoch, install.version,
                       install.release, install.arch)
                new_deps.append(dep)
        return self.dependency_cache.get_or_create_nevras(self.db, new_deps)

    def change_to_applied(self, change):
        applied_change = {
            'distance': change['distance'],
            'build_id': change['build_id'],
        }
        for state in ('prev', 'curr'):
            def s(x, prefix=state):
                return prefix + '_' + x
            if change[s('version')]:
                applied_change[s('dep_id')] = self.dependency_cache.get_or_create_nevra(
                    self.db,
                    (
                        change['dep_name'],
                        change[s('epoch')], change[s('version')],
                        change[s('release')], change[s('arch')],
                    )
                ).id
            else:
                applied_change[s('dep_id')] = None
        return applied_change
