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

from __future__ import print_function, absolute_import, division

import contextlib

import koji
import time

from collections import OrderedDict, namedtuple
from itertools import groupby
from six.moves import zip as izip

from sqlalchemy.orm import joinedload, undefer
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlalchemy.sql import insert

from koschei import util, backend
from koschei.config import get_config
from koschei.backend import koji_util, depsolve
from koschei.backend.service import Service
from koschei.backend.koji_util import itercall
from koschei.backend.repo_util import KojiRepoDescriptor
from koschei.models import (
    Package, Dependency, UnappliedChange, AppliedChange, ResolutionProblem,
    Build, BuildrootProblem, RepoMapping, ResolutionChange,
)
from koschei.plugin import dispatch_event
from koschei.util import Stopwatch, stopwatch

total_time = Stopwatch("Total repo generation")

DepTuple = namedtuple('DepTuple', ['id', 'name', 'epoch', 'version', 'release',
                                   'arch'])
ResolutionOutput = namedtuple('ResolutionOutput',
                              ['package', 'prev_resolved', 'resolved',
                               'problems', 'changes', 'last_build_id'])


@stopwatch(total_time)
def create_dependency_changes(deps1, deps2, **rest):
    if not deps1 or not deps2:
        # TODO packages with no deps
        return []

    def key(dep):
        return dep.name, dep.epoch, dep.version, dep.release, dep.arch

    def create_change(**values):
        new_change = dict(
            prev_version=None, prev_epoch=None, prev_release=None, prev_arch=None,
            curr_version=None, curr_epoch=None, curr_release=None, curr_arch=None,
        )
        new_change.update(rest)
        new_change.update(values)
        return new_change

    old = util.set_difference(deps1, deps2, key)
    new = util.set_difference(deps2, deps1, key)

    changes = {}
    for dependency in old:
        change = create_change(
            dep_name=dependency.name,
            prev_version=dependency.version, prev_epoch=dependency.epoch,
            prev_release=dependency.release, prev_arch=dependency.arch,
            distance=None,
        )
        changes[dependency.name] = change
    for dependency in new:
        change = changes.get(dependency.name) or create_change(dep_name=dependency.name)
        change.update(
            curr_version=dependency.version, curr_epoch=dependency.epoch,
            curr_release=dependency.release, curr_arch=dependency.arch,
            distance=dependency.distance,
        )
        changes[dependency.name] = change
    return list(changes.values()) if changes else []


class DependencyCache(object):
    def __init__(self, capacity):
        self.capacity = capacity
        self.nevras = {}
        self.ids = OrderedDict()

    def _add(self, dep):
        self.ids[dep.id] = dep
        self.nevras[(dep.name, dep.epoch, dep.version, dep.release,
                     dep.arch)] = dep
        if len(self.ids) > self.capacity:
            self._compact()

    def _access(self, dep):
        del self.ids[dep.id]
        self.ids[dep.id] = dep

    def _compact(self):
        _, victim = self.ids.popitem(last=False)
        # pylint: disable=no-member
        del self.nevras[(victim.name, victim.epoch, victim.version,
                         victim.release, victim.arch)]

    def get_or_create_nevra(self, db, nevra):
        dep = self.nevras.get(nevra)
        if dep is None:
            dep = db.query(*Dependency.inevra)\
                .filter((Dependency.name == nevra[0]) &
                        (Dependency.epoch == nevra[1]) &
                        (Dependency.version == nevra[2]) &
                        (Dependency.release == nevra[3]) &
                        (Dependency.arch == nevra[4]))\
                .first()
            if dep is None:
                kwds = dict(name=nevra[0], epoch=nevra[1], version=nevra[2],
                            release=nevra[3], arch=nevra[4])
                dep_id = db.execute(insert(Dependency, [kwds],
                                           returning=(Dependency.id,)))\
                    .fetchone().id
                dep = DepTuple(id=dep_id, **kwds)
            self._add(dep)
        else:
            self._access(dep)
        return dep

    def get_or_create_nevras(self, db, nevras):
        res = []
        for nevra in nevras:
            res.append(self.get_or_create_nevra(db, nevra))
        return res

    @stopwatch(total_time, note='dependency cache')
    def get_by_ids(self, db, ids):
        res = []
        missing = []
        for dep_id in ids:
            dep = self.ids.get(dep_id)
            if dep is None:
                missing.append(dep_id)
            else:
                res.append(dep)
                self._access(dep)
        if missing:
            deps = db.query(*Dependency.inevra).filter(Dependency.id.in_(missing)).all()
            for dep in deps:
                self._add(dep)
                res.append(dep)
        assert res
        return res


class RepoGenerationException(Exception):
    pass


class Resolver(Service):
    def __init__(self, session):
        super(Resolver, self).__init__(session)
        capacity = get_config('dependency.dependency_cache_capacity')
        self.dependency_cache = DependencyCache(capacity=capacity)

    def get_build_group(self, collection):
        group = koji_util.get_build_group_cached(
            self.session,
            self.session.koji('primary'),
            collection.build_tag,
            collection.build_group,
        )
        return group

    def store_deps(self, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = (install.name, install.epoch, install.version,
                       install.release, install.arch)
                new_deps.append(dep)
        return self.dependency_cache.get_or_create_nevras(self.db, new_deps)

    @stopwatch(total_time, note='separate thread')
    def resolve_dependencies(self, sack, br, build_group):
        deps = None
        resolved, problems, installs = depsolve.run_goal(sack, br, build_group)
        if resolved:
            problems = []
            deps = [
                depsolve.DependencyWithDistance(
                    name=pkg.name, epoch=pkg.epoch, version=pkg.version,
                    release=pkg.release, arch=pkg.arch,
                ) for pkg in installs if pkg.arch != 'src'
            ]
            depsolve.compute_dependency_distances(sack, br, deps)
        return resolved, problems, deps

    def get_prev_build_for_comparison(self, build):
        return self.db.query(Build)\
            .filter_by(package_id=build.package_id)\
            .filter(Build.started < build.started)\
            .filter(Build.deps_resolved == True)\
            .order_by(Build.started.desc())\
            .options(undefer('dependency_keys'))\
            .first()

    def set_descriptor_tags(self, collection, descriptors):
        def koji_call(koji_session, desc):
            koji_session.repoInfo(desc.repo_id)

        result_gen = itercall(self.session.secondary_koji_for(collection),
                              descriptors, koji_call)
        for descriptor, repo_info in izip(descriptors, result_gen):
            if repo_info['state'] in (koji.REPO_STATES['READY'],
                                      koji.REPO_STATES['EXPIRED']):
                descriptor.build_tag = repo_info['tag_name']
            else:
                self.log.info('Repo {} is dead, skipping'.format(descriptor.repo_id))

    @stopwatch(total_time)
    def get_build_for_comparison(self, package):
        """
        Returns newest build which should be used for dependency
        comparisons or None if it shouldn't be compared at all
        """
        last_build = package.last_build
        if last_build and last_build.state in Build.FINISHED_STATES:
            if last_build.deps_resolved is True:
                return last_build
            if last_build.deps_resolved is False:
                # unresolved build, skip it
                return self.get_prev_build_for_comparison(last_build)
            # not yet processed builds are not considered
            return None

    # pylint: disable=too-many-locals
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

    def generate_dependency_changes(self, sack, collection, packages, brs):
        """
        Generates and persists dependency changes for given list of packages.
        Emits package state change events.
        """
        results = []

        build_group = self.get_build_group(collection)
        gen = ((package, self.resolve_dependencies(sack, br, build_group))
               for package, br in izip(packages, brs))
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
                        self.db, prev_build.dependency_keys
                    )
                    changes = create_dependency_changes(
                        prev_deps, curr_deps, package_id=package.id,
                    )
                    # UnappliedChange doesn't contain arch
                    for change in changes:
                        del change['prev_arch']
                        del change['curr_arch']
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

    def resolve_repo(self, sack, collection, repo_id):
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
        build_group = self.get_build_group(collection)
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

    def resolve_packages(self, sack, collection, packages):
        """
        Generates new dependency changes for given packages
        Commits data in increments.
        """

        # get buildrequires
        brs = koji_util.get_rpm_requires_cached(
            self.session,
            self.session.secondary_koji_for(collection),
            [p.srpm_nvra for p in packages],
        )

        self.log.info(
            "Resolving dependencies (repo_id={}, collection={}) for {} packages"
            .format(
                collection.latest_repo_id,
                collection.name,
                len(packages),
            )
        )
        self.generate_dependency_changes(sack, collection, packages, brs)
        self.db.commit()

    @contextlib.contextmanager
    def prepared_repo(self, collection, repo_id):
        repo_descriptor = self.create_repo_descriptor(collection.secondary_mode, repo_id)
        self.set_descriptor_tags(collection, [repo_descriptor])
        if not repo_descriptor.build_tag:
            raise RepoGenerationException('Repo {} is dead'.format(repo_id))
        with self.session.repo_cache.get_sack(repo_descriptor) as sack:
            if not sack:
                raise RepoGenerationException(
                    'Cannot obtain repo sack (repo_id={})'.format(repo_id)
                )
            yield sack

    @staticmethod
    def create_repo_descriptor(secondary_mode, repo_id):
        return KojiRepoDescriptor('secondary' if secondary_mode else 'primary',
                                  None, repo_id)

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
                changes = create_dependency_changes(prev_deps, curr_deps,
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
            with self.prepared_repo(collection, repo_id) as sack:
                self.resolve_repo(sack, collection, repo_id)
                if collection.latest_repo_resolved:
                    packages = self.get_packages(collection)
                    self.resolve_packages(sack, collection, packages)
            total_time.stop()
            total_time.display()
        elif collection.latest_repo_resolved:
            # we don't have a new repo, but we can at least resolve new packages
            new_packages = self.get_packages(collection, only_new=True)
            if new_packages:
                with self.prepared_repo(collection, collection.latest_repo_id) as sack:
                    self.resolve_packages(sack, collection, new_packages)
