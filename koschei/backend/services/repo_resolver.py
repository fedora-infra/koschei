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

import contextlib

import koji
import time

from collections import namedtuple

from sqlalchemy.orm import joinedload, undefer
from sqlalchemy.sql import insert

from koschei import util, backend
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.plugin import dispatch_event
from koschei.util import stopwatch
from koschei.locks import pg_session_lock, Locked, LOCK_REPO_RESOLVER
from koschei.models import (
    Package, UnappliedChange, ResolutionProblem, BuildrootProblem, RepoMapping,
    ResolutionChange, Collection,
)

from koschei.backend.services.resolver import Resolver, total_time


class RepoGenerationException(Exception):
    pass


ResolutionOutput = namedtuple(
    'ResolutionOutput',
    ['package', 'prev_resolved', 'resolved', 'problems', 'changes', 'last_build_id'],
)


class RepoResolver(Resolver):
    def main(self):
        for collection in self.db.query(Collection).all():
            try:
                with pg_session_lock(
                    self.db, LOCK_REPO_RESOLVER, collection.id, block=False
                ):
                    self.process_repo(collection)
                    self.db.commit()
            except Locked:
                # Locked by another process
                continue

    def process_repo(self, collection):
        """
        Process repo for given collection.
        Repo processing means resolving all packages in new repo if such repo
        is available. Otherwise tries to at leas resolve newly added packages.
        """
        repo_id = self.get_new_repo_id(collection)

        if repo_id:
            # we have repo to resolve, so just try to resolve everything
            total_time.reset()
            total_time.start()
            self.dependency_cache.clear_stats()
            with self.prepared_repo(collection, repo_id) as sack:
                self.resolve_repo(collection, repo_id, sack)
                if collection.latest_repo_resolved:
                    packages = self.get_packages(collection)
                    self.resolve_packages(collection, repo_id, sack, packages)
            total_time.stop()
            total_time.display()
            self.log.info("Dependency cache stats: %s", self.dependency_cache.get_stats())
        elif collection.latest_repo_resolved:
            # we don't have a new repo, but we can at least resolve new packages
            new_packages = self.get_packages(collection, only_new=True)
            if new_packages:
                repo_id = collection.latest_repo_id
                with self.prepared_repo(collection, repo_id) as sack:
                    self.resolve_packages(collection, repo_id, sack, new_packages)

    def get_new_repo_id(self, collection):
        """
        Returns a latest repo id that is suitable for new repo resolution or None.

        :param: collection for which collection to query
        """
        latest_repo = koji_util.get_latest_repo(
            self.session.secondary_koji_for(collection),
            collection.build_tag,
        )

        if latest_repo and (not collection.latest_repo_id or
                            latest_repo.get('id', 0) > collection.latest_repo_id):
            if not collection.secondary_mode:
                return latest_repo['id']
            else:
                # in secondary mode, we want to only resolve the repo if it was
                # already regenerated on primary
                backend.refresh_repo_mappings(self.session)
                mapping = self.db.query(RepoMapping)\
                    .filter_by(secondary_id=latest_repo['id'])\
                    .first()
                if (
                        mapping and
                        mapping.primary_id and (
                            self.session.koji('primary')
                            .getTaskInfo(mapping.task_id)['state']
                        ) == koji.TASK_STATES['CLOSED']
                ):
                    return latest_repo['id']

    @contextlib.contextmanager
    def prepared_repo(self, collection, repo_id):
        repo_descriptor = self.create_repo_descriptor(collection, repo_id)
        if not repo_descriptor:
            raise RepoGenerationException('Repo {} is dead'.format(repo_id))
        with self.session.repo_cache.get_sack(repo_descriptor) as sack:
            if not sack:
                raise RepoGenerationException(
                    'Cannot obtain repo sack (repo_id={})'.format(repo_id)
                )
            yield sack

    def resolve_repo(self, collection, repo_id, sack):
        """
        Resolves given repo base buildroot. Stores buildroot problems if any.
        Updates collection metadata (latest_repo_id, latest_repo_resolved).
        Commits.

        :param: sack sack used for dependency resolution
        :param: collection collection to which the repo belongs
        :param: repo_id numeric id of the koji repo
        """
        self.log.info(
            "Generating new repo (repo_id={}, collection={})".format(
                repo_id,
                collection.name,
            )
        )
        build_group = self.get_build_group(collection, repo_id)
        resolved, base_problems, _ = self.resolve_dependencies(sack, [], build_group)
        self.db.query(BuildrootProblem)\
            .filter_by(collection_id=collection.id)\
            .delete()
        prev_state = collection.state_string
        collection.latest_repo_id = repo_id
        collection.latest_repo_resolved = resolved
        new_state = collection.state_string
        if not resolved:
            self.log.info("Build group not resolvable for {}"
                          .format(collection.name))
            self.db.execute(BuildrootProblem.__table__.insert(),
                            [{'collection_id': collection.id, 'problem': problem}
                             for problem in base_problems])
        self.db.commit()
        dispatch_event('collection_state_change', self.session,
                       collection=collection, prev_state=prev_state, new_state=new_state)

    def get_packages(self, collection, only_new=False):
        """
        Get packages eligible for resolution in new repo for given collection.

        :param: collection collection for which packages are requested
        :param: only_new whether to consider only packages that weren't
                         resolved yet
        """
        query = (
            self.db.query(Package)
            .filter(~Package.blocked)
            .filter(Package.tracked)
            .filter(~Package.skip_resolution)
            .filter(Package.collection_id == collection.id)
            .filter(Package.last_complete_build_id != None)
            .options(joinedload(Package.last_build))
            .options(undefer('last_build.dependency_keys'))
        )
        if only_new:
            query = query.filter(Package.resolved == None)
        return query.all()

    def resolve_packages(self, collection, repo_id, sack, packages):
        """
        Generates new dependency changes for given packages
        Commits data in increments.
        """

        # get buildrequires
        brs = self.get_rpm_requires(
            collection,
            [p.srpm_nvra for p in packages],
        )

        self.log.info(
            "Resolving dependencies (repo_id={}, collection={}) for {} packages"
            .format(
                repo_id,
                collection.name,
                len(packages),
            )
        )
        self.generate_dependency_changes(collection, repo_id, sack, packages, brs)
        self.db.commit()

    def generate_dependency_changes(self, collection, repo_id, sack, packages, brs):
        """
        Generates and persists dependency changes for given list of packages.
        Emits package state change events.
        """
        # pylint:disable=too-many-locals
        results = []

        build_group = self.get_build_group(collection, repo_id)
        if build_group is None:
            raise RuntimeError(
                f"No build group found for {collection.name} at repo_id {repo_id}"
            )
        gen = ((package, self.resolve_dependencies(sack, br, build_group))
               for package, br in zip(packages, brs))
        queue_size = get_config('dependency.resolver_queue_size')
        gen = util.parallel_generator(gen, queue_size=queue_size)
        pkgs_done = 0
        pkgs_reported = 0
        progres_reported_at = time.time()
        for package, (resolved, curr_problems, curr_deps) in gen:
            changes = []
            if curr_deps is not None:
                prev_build = self.get_build_for_comparison(package)
                if prev_build and prev_build.dependency_keys:
                    prev_deps = self.dependency_cache.get_by_ids(
                        prev_build.dependency_keys
                    )
                    changes = self.create_dependency_changes(
                        prev_deps, curr_deps, package_id=package.id,
                    )
            results.append(ResolutionOutput(
                package=package,
                prev_resolved=package.resolved,
                resolved=resolved,
                problems=set(curr_problems),
                changes=changes,
                # last_build_id is used to detect concurrently registered builds
                last_build_id=package.last_build_id,
            ))
            if len(results) > get_config('dependency.persist_chunk_size'):
                self.persist_resolution_output(results)
                results = []
            pkgs_done += 1
            current_time = time.time()
            time_diff = current_time - progres_reported_at
            if time_diff > get_config('dependency.perf_report_interval'):
                self.log.info(
                    "Resolution progress: resolved {} packages ({}%) ({} pkgs/min)"
                    .format(
                        pkgs_done,
                        int(pkgs_done / len(packages) * 100.0),
                        int((pkgs_done - pkgs_reported) / time_diff * 60.0)

                    )
                )
                pkgs_reported = pkgs_done
                progres_reported_at = current_time

        self.persist_resolution_output(results)

    @stopwatch(total_time)
    def persist_resolution_output(self, chunk):
        """
        Stores resolution output into the database and sends fedmsg if needed.

        chunk format:
        [
            ResolutionOutput(
                package=Package(...),
                prev_resolved=False,
                resolved=True,  # current resolution status
                changes=[dict(...), ...],  # dependency changes in dict form
                problems={dict(...), ...},  # dependency problems in dict form,
                                            # note it's a set
                last_build_id=456,  # used to detect concurrently inserted builds
            ),
        ...]
        """
        if not chunk:
            return

        package_ids = [p.package.id for p in chunk]

        # expire packages, so that we get the packages we locked, not old
        # version in sqla cache
        for p in chunk:
            self.db.expire(p.package)

        # lock the packages to be updated
        (
            self.db.query(Package.id)
            .filter(Package.id.in_(package_ids))
            .order_by(Package.id)  # ordering to prevent deadlocks
            .with_lockmode('update')
            .all()
        )

        # find latest resolution problems to be compared for change
        previous_problems = {
            r.package_id: set(p.problem for p in r.problems)
            for r in self.db.query(ResolutionChange)
            .filter(ResolutionChange.package_id.in_(package_ids))
            .options(joinedload(ResolutionChange.problems))
            .order_by(ResolutionChange.package_id,
                      ResolutionChange.timestamp.desc())
            .distinct(ResolutionChange.package_id)
            .all()
        }

        # dependency problems to be persisted
        # format: [tuple(resolution change (orm objects), problems (strings))]
        problem_entries = []
        # dependency changes to be persisted
        dependency_changes = []

        # state changes for fedmsg. Message sending should be done after commit
        # format: a dict from id -> (prev_state: string, new_state: string)
        state_changes = {}

        update_weight = get_config('priorities.package_update')

        # update packages, queue resolution results, changes and problems for insertion
        for pkg_result in chunk:
            package = pkg_result.package

            if pkg_result.last_build_id != package.last_build_id:
                # there was a build submitted/registered in the meantime,
                # our results are likely outdated -> discard them
                continue

            # get state before update
            prev_state = package.msg_state_string
            package.resolved = pkg_result.resolved
            # get state after update
            new_state = package.msg_state_string
            # compute dependency priority
            package.dependency_priority = int(
                sum(
                    update_weight / (change['distance'] or 8)
                    for change in pkg_result.changes
                )
            )
            if prev_state != new_state:
                # queue for fedmsg sending after commit
                state_changes[package.id] = prev_state, new_state

            dependency_changes += pkg_result.changes

            # compare whether there was any change from the previous state
            # - we should emit a new resolution change only if the resolution
            # state or the set of dependency problems changed
            if (
                    pkg_result.prev_resolved != pkg_result.resolved or (
                        pkg_result.resolved is False and
                        pkg_result.prev_resolved is False and
                        # both are sets, they can be compared directly
                        pkg_result.problems != previous_problems.get(package.id)
                    )
            ):
                resolution_change = ResolutionChange(
                    package_id=package.id,
                    resolved=pkg_result.resolved,
                )
                self.db.add(resolution_change)
                problem_entries.append((resolution_change, pkg_result.problems))

        # populate resolution changes' ids
        self.db.flush()

        # set problem resolution_ids and prepare dict form
        to_insert = [
            dict(resolution_id=resolution_change.id, problem=problem)
            for resolution_change, problems in problem_entries
            for problem in problems
        ]

        # insert dependency problems
        if to_insert:
            self.db.execute(insert(ResolutionProblem, to_insert))

        # delete old dependency changes, they'll be replaced with new ones
        self.db.query(UnappliedChange)\
            .filter(UnappliedChange.package_id.in_(package_ids))\
            .delete()

        # insert dependency changes
        if dependency_changes:
            self.db.execute(insert(UnappliedChange, dependency_changes))

        self.db.commit_no_expire()

        # emit fedmsg (if enabled)
        if state_changes:
            for package in self.db.query(Package)\
                .filter(Package.id.in_(state_changes))\
                .options(joinedload(Package.groups),
                         joinedload(Package.collection)):
                prev_state, new_state = state_changes[package.id]
                dispatch_event(
                    'package_state_change',
                    self.session,
                    package=package,
                    prev_state=prev_state,
                    new_state=new_state,
                )
