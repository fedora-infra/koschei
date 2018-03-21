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

from itertools import groupby

from sqlalchemy.sql import insert
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from koschei.config import get_config
from koschei.locks import pg_session_lock, Locked, LOCK_BUILD_RESOLVER
from koschei.models import (
    Collection, Package, AppliedChange, Build,
)
from koschei.backend.depsolve import Solver

from koschei.backend.services.resolver import Resolver


class BuildResolver(Resolver):
    """
    Service for processing dependencies of builds.
    """

    def main(self):
        """
        Service entry-point. Processes builds in all collections.
        """
        for collection in self.db.query(Collection).all():
            self.process_builds(collection)

    def process_builds(self, collection):
        """
        Processes builds in a single collection.
        Can be executed concurrently from multiple processes. One process
        always processes one repo ID.
        Commits the transaction in increments.
        """
        builds = (
            self.db.query(Build)
            .join(Build.package)
            .filter(Build.deps_resolved == None)
            .filter(Build.repo_id != None)
            .filter(Package.collection_id == collection.id)
            .order_by(Build.repo_id)
            .all()
        )

        if not builds:
            self.log.debug("No builds to process for collection %s", collection)
            return

        self.log.info("Processing %d builds for collection %s", len(builds), collection)

        # Group by repo_id to speed up processing (reuse the sack)
        for repo_id, builds_group in groupby(builds, lambda b: b.repo_id):
            builds_group = list(builds_group)
            try:
                with pg_session_lock(self.db, LOCK_BUILD_RESOLVER, repo_id, block=False):
                    self.process_builds_with_repo_id(collection, repo_id, builds_group)
                    self.db.commit()
            except Locked:
                continue

    def process_builds_with_repo_id(self, collection, repo_id, builds):
        """
        Processes given builds in a single collection assuming a single repo_id.
        Commits the transaction in increments.
        """
        self.log.info("Processing builds for repo ID %d", repo_id)
        descriptor = self.create_repo_descriptor(collection, repo_id)
        if not descriptor:
            self.log.info("Repo ID %d is dead. Skipping.", repo_id)
            # Builds with no repo cannot be resolved
            self.process_unresolved_builds(builds)
            return

        build_group = self.get_build_group(collection, repo_id)
        if not build_group:
            self.log.info("Failed to obtain build group for repo ID %d", repo_id)
            self.process_unresolved_builds(builds)
            return

        with self.session.repo_cache.get_sack(descriptor) as sack:
            if not sack:
                self.log.info("Failed to obtain sack for repo ID %d", repo_id)
                # The repo was not marked as deleted in Koji, so this is likely
                # a temporary failure, which will be retried on the next cycle
                return
            solver = Solver(sack)
            buildroot = solver.buildroot(build_group)
            nvras = [b.srpm_nvra for b in builds]
            all_brs = self.get_rpm_requires(collection, nvras)
            for build, brs in zip(builds, all_brs):
                self.process_build(buildroot, build, brs)

    def process_build(self, solver, build, brs):
        """
        Processes single build in given sack.
        Commits the transaction.
        """
        self.log.info("Processing %s", build)
        try:
            resolved, _, installs = self.resolve_dependencies(solver, brs)
            if not resolved:
                self.process_unresolved_build(build)
            else:
                self.process_resolved_build(build, installs)
            self.db.commit()
        except (StaleDataError, ObjectDeletedError):
            # build deleted concurrently, can be skipped
            self.db.rollback()

    def process_unresolved_builds(self, builds):
        """
        Calls process_unresolved_build for multiple builds
        """
        for build in builds:
            self.process_unresolved_build(build)

    def process_unresolved_build(self, build):
        """
        This function marks the build as unresolved and bumps priority of
        packages that have given builds as last.
        Packages that have last build unresolved cannot be resolved and
        should be treated as new packages (which they most likely are),
        because they cannot get priority from dependencies.
        """
        build.deps_resolved = False
        if build.package.last_build_id == build.id:
            build.package.build_priority = get_config('priorities.newly_added')

    def process_resolved_build(self, build, curr_deps):
        """
        Processes a single build that resolved suceessfully.
        That entails marking it as resolved, storing the dependencies and
        dependency changes.
        """
        build.deps_resolved = True
        self.store_dependencies(build, curr_deps)
        prev_build = self.get_prev_build_for_comparison(build)
        if prev_build and prev_build.deps_resolved:
            prev_deps = self.get_build_dependencies(prev_build)
            if prev_deps is not None:
                changes = self.create_dependency_changes(
                    prev_deps, curr_deps,
                    build_id=build.id,
                )
                if changes:
                    applied_changes = [self.change_to_applied(c) for c in changes]
                    self.db.execute(insert(AppliedChange, applied_changes))
            prev_build.dependency_keys = None

    def store_dependencies(self, build, installs):
        """
        Stores a list of dependencies (output of hawkey.Goal.list_installs)
        as dependencies of given build.
        """
        dep_tuples = [
            (
                install.name, install.epoch, install.version, install.release,
                install.arch,
            ) for install in installs
            if install.arch != 'src'
        ]
        deps = self.dependency_cache.get_or_create_nevras(self.db, dep_tuples)
        build.dependency_keys = [dep.id for dep in deps]

    def get_build_dependencies(self, build):
        """
        Fetches dependencies of a given build.
        """
        if build.dependency_keys:
            return self.dependency_cache.get_by_ids(self.db, build.dependency_keys)

    def change_to_applied(self, change):
        """
        Converts a single item from output of create_dependency_changes to
        dicts suitable for AppliedChange insert.
        """
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
