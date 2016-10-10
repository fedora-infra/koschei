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

from collections import OrderedDict, namedtuple
from itertools import izip, groupby

import koji
from koschei.backend.koji_util import itercall
from koschei.backend.service import KojiService
from sqlalchemy.orm import joinedload, undefer
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlalchemy.sql import insert

from koschei import util
from koschei.config import get_config
from koschei.backend import Backend, koji_util, depsolve
from koschei.backend.repo_cache import RepoCache, RepoDescriptor
from koschei.models import (Package, Dependency, UnappliedChange,
                            AppliedChange, Collection, ResolutionProblem,
                            Build, BuildrootProblem, RepoMapping,
                            ResolutionChange)
from koschei.plugin import dispatch_event
from koschei.util import Stopwatch

total_time = Stopwatch("Total repo generation")
resolution_time = Stopwatch("Dependency resolution", total_time)
resolve_dependencies_time = Stopwatch("resolve_dependencies", resolution_time)
create_dependency_changes_time = Stopwatch("create_dependency_changes", resolution_time)
generate_dependency_changes_time = Stopwatch("generate_dependency_changes")


DepTuple = namedtuple('DepTuple', ['id', 'name', 'epoch', 'version', 'release',
                                   'arch'])
ResolutionOutput = namedtuple('ResolutionOutput',
                              ['package_id', 'prev_resolved', 'resolved',
                               'problems', 'changes'])


class DependencyWithDistance(object):
    def __init__(self, name, epoch, version, release, arch):
        self.name = name
        self.epoch = epoch
        self.version = version
        self.release = release
        self.arch = arch
        self.distance = None


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

    def _get_or_create(self, db, nevra):
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
            res.append(self._get_or_create(db, nevra))
        return res

    def get_by_ids(self, db, ids):
        res = []
        missing = []
        # pylint:disable=redefined-builtin
        for id in ids:
            dep = self.ids.get(id)
            if dep is None:
                missing.append(id)
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


class Resolver(KojiService):
    def __init__(self, log=None, db=None, koji_sessions=None,
                 repo_cache=None, backend=None):
        super(Resolver, self).__init__(log=log, db=db,
                                       koji_sessions=koji_sessions)
        self.repo_cache = repo_cache or RepoCache()
        self.backend = backend or Backend(koji_sessions=self.koji_sessions,
                                          log=self.log, db=self.db)
        self.build_groups = {}
        capacity = get_config('dependency.dependency_cache_capacity')
        self.dependency_cache = DependencyCache(capacity=capacity)

    def get_build_group(self, collection):
        group = self.build_groups.get(collection.id)
        if group is None:
            group = koji_util.get_build_group(self.koji_sessions['primary'],
                                              collection.build_tag,
                                              collection.build_group)
            self.build_groups[collection.id] = group
        assert group
        return group

    def store_deps(self, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = (install.name, install.epoch, install.version,
                       install.release, install.arch)
                new_deps.append(dep)
        return self.dependency_cache.get_or_create_nevras(self.db, new_deps)

    def resolve_dependencies(self, sack, br, build_group):
        resolve_dependencies_time.start()
        deps = None
        resolved, problems, installs = depsolve.run_goal(sack, br, build_group)
        if resolved:
            problems = []
            deps = [DependencyWithDistance(name=pkg.name, epoch=pkg.epoch,
                                           version=pkg.version, release=pkg.release,
                                           arch=pkg.arch)
                    for pkg in installs if pkg.arch != 'src']
            depsolve.compute_dependency_distances(sack, br, deps)
        resolve_dependencies_time.stop()
        return (resolved, problems, deps)

    def create_dependency_changes(self, deps1, deps2, **rest):
        if not deps1 or not deps2:
            # TODO packages with no deps
            return []

        def key(dep):
            return (dep.name, dep.epoch, dep.version, dep.release)

        def new_change(**values):
            change = dict(prev_version=None, prev_epoch=None,
                          prev_release=None, curr_version=None,
                          curr_epoch=None, curr_release=None)
            change.update(rest)
            change.update(values)
            return change

        old = util.set_difference(deps1, deps2, key)
        new = util.set_difference(deps2, deps1, key)

        changes = {}
        for dep in old:
            change = new_change(dep_name=dep.name,
                                prev_version=dep.version, prev_epoch=dep.epoch,
                                prev_release=dep.release, distance=None)
            changes[dep.name] = change
        for dep in new:
            change = changes.get(dep.name) or new_change(dep_name=dep.name)
            change.update(curr_version=dep.version, curr_epoch=dep.epoch,
                          curr_release=dep.release, distance=dep.distance)
            changes[dep.name] = change
        return changes.values() if changes else []

    def get_prev_build_for_comparison(self, build):
        return self.db.query(Build)\
            .filter_by(package_id=build.package_id)\
            .filter(Build.id < build.id)\
            .filter(Build.deps_resolved == True)\
            .order_by(Build.id.desc())\
            .options(undefer('dependency_keys'))\
            .first()

    def set_descriptor_tags(self, collection, descriptors):
        def koji_call(koji_session, desc):
            koji_session.repoInfo(desc.repo_id)
        result_gen = itercall(self.secondary_session_for(collection),
                              descriptors, koji_call)
        for desc, repo_info in izip(descriptors, result_gen):
            if repo_info['state'] in (koji.REPO_STATES['READY'],
                                      koji.REPO_STATES['EXPIRED']):
                desc.build_tag = repo_info['tag_name']
            else:
                self.log.info('Repo {} is dead, skipping'.format(desc.repo_id))

    def get_packages(self, collection, expunge=True):
        packages = self.db.query(Package)\
            .filter(~Package.blocked)\
            .filter(~Package.skip_resolution)\
            .filter_by(collection_id=collection.id)\
            .filter(Package.tracked == True)\
            .filter(Package.last_complete_build_id != None)\
            .options(joinedload(Package.last_build))\
            .options(joinedload(Package.last_complete_build))\
            .options(undefer('*'))\
            .all()
        # detaches objects from ORM, prevents spurious queries that hinder
        # performance
        if expunge:
            for p in packages:
                self.db.expunge(p)
                self.db.expunge(p.last_build)
                if p.last_build is not p.last_complete_build:
                    self.db.expunge(p.last_complete_build)
        return packages

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

    # pylint: disable=too-many-locals
    def persist_resolution_output(self, chunk):
        if not chunk:
            return
        package_ids = [p.package_id for p in chunk]
        self.db.query(Package)\
            .filter(Package.id.in_(package_ids))\
            .lock_rows()
        self.db.query(UnappliedChange)\
            .filter(UnappliedChange.package_id.in_(package_ids))\
            .delete()

        already_unresolved = self.db.query(ResolutionChange)\
            .filter(ResolutionChange.package_id.in_(package_ids))\
            .options(joinedload(ResolutionChange.problems))\
            .order_by(ResolutionChange.package_id,
                      ResolutionChange.timestamp.desc())\
            .distinct(ResolutionChange.package_id)\
            .all()

        problems = []
        changes = []
        problem_map = {r.package_id: set(p.problem for p in r.problems)
                       for r in already_unresolved}
        for pkg in chunk:
            if (pkg.prev_resolved != pkg.resolved or
                    pkg.resolved is False and pkg.prev_resolved is False and
                    pkg.problems != problem_map[pkg.package_id]):
                result = ResolutionChange(package_id=pkg.package_id,
                                          resolved=pkg.resolved)
                self.db.add(result)
                self.db.flush()
                problems += [dict(resolution_id=result.id, problem=problem)
                             for problem in pkg.problems]
            changes += pkg.changes
        if problems:
            self.db.execute(insert(ResolutionProblem, problems))

        self.db.query(UnappliedChange)\
            .filter(UnappliedChange.package_id.in_(package_ids))\
            .delete()
        if changes:
            self.db.execute(insert(UnappliedChange, changes))

        changed = {r.package_id: r for r in chunk
                   if r.resolved != r.prev_resolved}
        for val in True, False:
            to_update = [r.package_id for r in chunk if r.resolved is val]
            if to_update:
                self.db.query(Package)\
                    .filter(Package.id.in_(to_update))\
                    .update({'resolved': val})
        packages = self.db.query(Package)\
            .filter(Package.id.in_(changed.iterkeys()))\
            .options(joinedload(Package.last_complete_build))\
            .all() if changed else []
        state_changes = {}
        for pkg in packages:
            self.db.expunge(pkg)
            pkg.resolved = changed[pkg.id].prev_resolved
            prev_state = pkg.msg_state_string
            pkg.resolved = changed[pkg.id].resolved
            new_state = pkg.msg_state_string
            if prev_state != new_state:
                state_changes[pkg.id] = prev_state, new_state

        self.db.commit()

        if state_changes:
            for package in self.db.query(Package)\
                .filter(Package.id.in_(state_changes.iterkeys()))\
                .options(joinedload(Package.groups),
                         joinedload(Package.collection)):
                prev_state, new_state = state_changes[package.id]
                dispatch_event('package_state_change', package=package,
                               prev_state=prev_state,
                               new_state=new_state)

    def generate_dependency_changes(self, sack, collection, packages, brs, repo_id):
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
        for package, (resolved, curr_problems, curr_deps) in gen:
            generate_dependency_changes_time.start()
            changes = []
            if curr_deps is not None:
                prev_build = self.get_build_for_comparison(package)
                if prev_build and prev_build.dependency_keys:
                    prev_deps = self.dependency_cache.get_by_ids(
                        self.db, prev_build.dependency_keys)
                    create_dependency_changes_time.start()
                    changes = self.create_dependency_changes(
                        prev_deps, curr_deps, package_id=package.id,
                        prev_build_id=prev_build.id)
                    create_dependency_changes_time.stop()
            results.append(ResolutionOutput(
                package_id=package.id,
                prev_resolved=package.resolved,
                resolved=resolved,
                problems=set(curr_problems),
                changes=changes,
            ))
            if len(results) > get_config('dependency.persist_chunk_size'):
                self.persist_resolution_output(results)
                results = []
            generate_dependency_changes_time.stop()
        self.persist_resolution_output(results)

    def generate_repo(self, collection, repo_id):
        """
        Generates new dependency changes for requested repo using given
        collection. Finishes early when base buildroot is not resolvable.
        Updates collection resolution metadata (repo_id, base_resolved) after
        finished. Commits data in increments.
        """
        total_time.reset()
        generate_dependency_changes_time.reset()
        total_time.start()
        self.log.info("Generating new repo")
        repo_descriptor = self.create_repo_descriptor(collection.secondary_mode, repo_id)
        self.set_descriptor_tags(collection, [repo_descriptor])
        if not repo_descriptor.build_tag:
            self.log.error('Cannot generate repo: {}'.format(repo_id))
            self.db.rollback()
            return
        packages = self.get_packages(collection)
        brs = koji_util.get_rpm_requires(
            self.secondary_session_for(collection),
            [p.srpm_nvra for p in packages]
        )
        brs = util.parallel_generator(brs, queue_size=None)
        try:
            sack = self.repo_cache.get_sack(repo_descriptor)
            if not sack:
                self.log.error('Cannot generate repo: {}'.format(repo_id))
                self.db.rollback()
                return
            build_group = self.get_build_group(collection)
            resolved, base_problems, _ = self.resolve_dependencies(sack, [], build_group)
            resolution_time.stop()
            self.db.query(BuildrootProblem)\
                .filter_by(collection_id=collection.id)\
                .delete()
            collection.latest_repo_resolved = resolved
            if not resolved:
                self.log.info("Build group not resolvable for {}"
                              .format(collection.name))
                collection.latest_repo_id = repo_id
                self.db.execute(BuildrootProblem.__table__.insert(),
                                [{'collection_id': collection.id, 'problem': problem}
                                 for problem in base_problems])
                self.db.commit()
                return
            self.db.commit()
            self.log.info("Resolving dependencies...")
            resolution_time.start()
            self.generate_dependency_changes(sack, collection, packages, brs, repo_id)
            resolution_time.stop()
        finally:
            brs.stop()
        collection.latest_repo_id = repo_id
        self.db.commit()
        total_time.stop()
        total_time.display()
        generate_dependency_changes_time.display()

    def create_repo_descriptor(self, secondary_mode, repo_id):
        return RepoDescriptor('secondary' if secondary_mode else 'primary',
                              None, repo_id)

    def process_build(self, sack, entry, curr_deps):
        self.log.info("Processing build {}".format(entry.id))
        prev = self.get_prev_build_for_comparison(entry)
        deps = self.store_deps(curr_deps)
        self.db.query(Build).filter_by(id=entry.id)\
            .update({'deps_resolved': curr_deps is not None,
                     'dependency_keys': [dep.id for dep in deps]})
        if curr_deps is None:
            return
        if prev and prev.dependency_keys:
            prev_deps = self.dependency_cache.get_by_ids(self.db, prev.dependency_keys)
            if prev_deps and curr_deps:
                changes = self.create_dependency_changes(prev_deps, curr_deps,
                                                         build_id=entry.id,
                                                         prev_build_id=prev.id)
                if changes:
                    self.db.execute(AppliedChange.__table__.insert(), changes)
        self.db.query(Build)\
            .filter_by(package_id=entry.package_id)\
            .filter(Build.repo_id < entry.repo_id)\
            .update({'dependency_keys': None})

    def process_builds(self, collection):
        # pylint: disable=E1101
        builds = self.db.query(Build.id, Build.repo_id, Build.real, Build.package_id,
                               Package.name, Build.version, Build.release,
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
                .update({'deps_resolved': False}, synchronize_session=False)
            self.db.commit()
        buildrequires = koji_util.get_rpm_requires(
            self.secondary_session_for(collection),
            [dict(name=b.name, version=b.version, release=b.release, arch='src')
             for b in builds_to_process]
        )
        if len(builds) > 100:
            buildrequires = util.parallel_generator(buildrequires, queue_size=None)
        for repo_descriptor, group in groupby(izip(repos_to_process,
                                                   builds_to_process,
                                                   buildrequires),
                                              lambda item: item[0]):
            sack = self.repo_cache.get_sack(repo_descriptor)
            if sack:
                for _, build, brs in group:
                    build_group = self.get_build_group(collection)
                    _, _, curr_deps = self.resolve_dependencies(sack, brs, build_group)
                    try:
                        self.process_build(sack, build, curr_deps)
                        self.db.commit()
                    except (StaleDataError, ObjectDeletedError):
                        # build deleted concurrently
                        self.db.rollback()
            else:
                self.log.info("Repo id=%d not available, skipping",
                              repo_descriptor.repo_id)
            sack = None
        self.db.query(Build)\
            .filter_by(repo_id=None)\
            .filter(Build.state.in_(Build.FINISHED_STATES))\
            .update({'deps_resolved': False}, synchronize_session=False)
        self.db.commit()

    def main(self):
        for collection in self.db.query(Collection).all():
            self.process_builds(collection)
            curr_repo = koji_util.get_latest_repo(
                self.secondary_session_for(collection),
                collection.build_tag)
            if curr_repo and collection.secondary_mode:
                self.backend.refresh_repo_mappings()
                mapping = self.db.query(RepoMapping)\
                    .filter_by(secondary_id=curr_repo['id'])\
                    .first()
                # don't resolve it if we don't have it on primary yet
                if not (mapping and mapping.primary_id and
                        self.koji_sessions['primary']
                        .getTaskInfo(mapping.task_id)['state'] ==
                        koji.TASK_STATES['CLOSED']):
                    continue
            if curr_repo and curr_repo['id'] > collection.latest_repo_id:
                self.generate_repo(collection, curr_repo['id'])
