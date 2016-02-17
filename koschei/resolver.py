# Copyright (C) 2014-2015  Red Hat, Inc.
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

import koji

from sqlalchemy.orm import joinedload

from itertools import izip, groupby
from collections import defaultdict

from koschei.models import (Package, Dependency, UnappliedChange,
                            AppliedChange, Collection, ResolutionProblem,
                            Build, BuildrootProblem)
from koschei import util
from koschei.util import Stopwatch
from koschei.service import KojiService
from koschei.repo_cache import RepoCache, RepoDescriptor
from koschei.backend import check_package_state


total_time = Stopwatch("Total repo generation")
resolution_time = Stopwatch("Dependency resolution", total_time)
resolve_dependencies_time = Stopwatch("resolve_dependencies", resolution_time)
create_dependency_changes_time = Stopwatch("create_dependency_changes", resolution_time)
generate_dependency_changes_time = Stopwatch("generate_dependency_changes")
fetch_dependencies_generator_time = Stopwatch("fetch_dependencies_generator")


class AbstractResolverTask(object):
    def __init__(self, log, db, koji_sessions, repo_cache):
        self.log = log
        self.db = db
        self.koji_sessions = koji_sessions
        self.repo_cache = repo_cache
        # TODO repo_id
        self.group = util.get_build_group(koji_sessions['primary'])

    def get_koji_session_for_build(self, build):
        return self.koji_sessions['secondary' if build.real else 'primary']

    def store_deps(self, build_id, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = Dependency(build_id=build_id,
                                 name=install.name, epoch=install.epoch,
                                 version=install.version,
                                 release=install.release,
                                 arch=install.arch)
                new_deps.append(dep)

        if new_deps:
            # pylint: disable=E1101
            table = Dependency.__table__
            dicts = [{c.name: getattr(dep, c.name) for c in table.c
                      if not c.primary_key}
                     for dep in new_deps]
            self.db.connection().execute(table.insert(), dicts)
            self.db.expire_all()

    def resolve_dependencies(self, sack, br):
        resolve_dependencies_time.start()
        deps = None
        resolved, problems, installs = util.run_goal(sack, self.group, br)
        if resolved:
            problems = []
            deps = [Dependency(name=pkg.name, epoch=pkg.epoch,
                               version=pkg.version, release=pkg.release,
                               arch=pkg.arch)
                    for pkg in installs if pkg.arch != 'src']
            util.compute_dependency_distances(sack, br, deps)
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
                      .order_by(Build.id.desc()).first()

    def set_descriptor_tags(self, descriptors):
        def select_session(desc):
            return self.koji_sessions[desc.koji_id]
        def koji_call(koji_session, desc):
            koji_session.repoInfo(desc.repo_id)
        result_gen = util.selective_itercall(select_session, descriptors, koji_call)
        for desc, repo_info in izip(descriptors, result_gen):
            if repo_info['state'] in (koji.REPO_STATES['READY'],
                                      koji.REPO_STATES['EXPIRED']):
                desc.build_tag = repo_info['tag_name']
            else:
                self.log.debug('Repo {} is dead, skipping'.format(desc.repo_id))

class GenerateRepoTask(AbstractResolverTask):

    def get_packages(self, collection, expunge=True, require_build=False):
        query = self.db.query(Package).filter(Package.blocked == False)\
                       .filter_by(collection_id=collection.id)\
                       .filter(Package.tracked == True)
        if require_build:
            query = query.filter(Package.last_complete_build_id != None)
        packages = query.options(joinedload(Package.last_build))\
                        .options(joinedload(Package.last_complete_build))\
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

    def check_package_state_changes(self, resolved_map):
        """
        Emits package state change events for packages that changed.
        Needs to be called before the change is persisted.

        :param resolved_map: dict from package ids to their new resolution state
        """
        packages = self.db.query(Package)\
            .filter(Package.id.in_(resolved_map.iterkeys()))\
            .options(joinedload(Package.last_complete_build))\
            .options(joinedload(Package.groups))
        for pkg in packages:
            self.db.expunge(pkg) # don't propagate the write, we'll do it manually later
            prev_state = pkg.msg_state_string
            pkg.resolved = resolved_map[pkg.id]
            check_package_state(pkg, prev_state)

    def get_build_for_comparison(self, package):
        """
        Returns newest build which should be used for dependency
        comparisons or None if it shouldn't be compared at all
        """
        last_build = package.last_build
        if last_build and last_build.state in Build.FINISHED_STATES:
            if last_build.deps_resolved:
                return last_build
            if last_build.deps_processed:
                # unresolved build, skip it
                return self.get_prev_build_for_comparison(last_build)

    def persist_results(self, resolved_map, problems, changes):
        """
        Persists resolution results into DB.

        :param resolved_map: dict from package ids to their new resolution state
        :param problems: list of dependency problems as dicts
        :param changes: list of dependency changes as dicts
        """
        if not resolved_map:
            return
        self.db.query(Package)\
            .filter(Package.id.in_(resolved_map.keys()))\
            .lock_rows()
        for val in True, False:
            pkg_ids = [pkg_id for pkg_id, resolved
                       in resolved_map.iteritems() if resolved is val]
            if pkg_ids:
                self.db.query(Package)\
                    .filter(Package.id.in_(pkg_ids))\
                    .update({'resolved': val}, synchronize_session=False)
        for rel, vals in (ResolutionProblem, problems), (UnappliedChange, changes):
            self.db.query(rel)\
                .filter(rel.package_id.in_(resolved_map.iterkeys()))\
                .delete(synchronize_session=False)
            if vals:
                self.db.execute(rel.__table__.insert(), vals)

    def fetch_dependencies_generator(self, packages):
        fetch_dependencies_generator_time.start()
        chunk_size = util.config['dependency']['dependency_fetch_chunk_size']
        while packages:
            current_packages = packages[:chunk_size]
            builds = [self.get_build_for_comparison(package) for package
                      in current_packages]
            build_to_package = {b.id: b.package_id for b in builds if b}
            deps_per_package = defaultdict(list)
            for dep in self.db.query(Dependency.build_id, *Dependency.nevra)\
                                .filter(Dependency.build_id.in_(build_to_package.keys())):
                deps_per_package[build_to_package[dep.build_id]].append(dep)
            fetch_dependencies_generator_time.stop()
            for package in current_packages:
                yield deps_per_package[package.id]
            fetch_dependencies_generator_time.start()
            packages = packages[chunk_size:]
        fetch_dependencies_generator_time.stop()

    def generate_dependency_changes(self, sack, packages, brs, repo_id):
        """
        Generates and persists dependency changes for given list of packages.
        Emits package state change events.
        """
        resolved_map = {}
        problems = []
        changes = []
        def persist():
            self.check_package_state_changes(resolved_map)
            self.persist_results(resolved_map, problems, changes)
            self.db.commit()
        gen = ((package, self.resolve_dependencies(sack, br))
               for package, br in izip(packages, brs))
        queue_size = util.config['dependency']['resolver_queue_size']
        gen = util.parallel_generator(gen, queue_size=queue_size)
        deps_fetcher = self.fetch_dependencies_generator(packages)
        for (package, result), prev_deps in izip(gen, deps_fetcher):
            generate_dependency_changes_time.start()
            resolved_map[package.id], curr_problems, curr_deps = result
            problems += [dict(package_id=package.id, problem=problem)
                         for problem in sorted(set(curr_problems))]
            if curr_deps is not None and prev_deps:
                create_dependency_changes_time.start()
                changes += self.create_dependency_changes(
                    prev_deps, curr_deps, package_id=package.id,
                    prev_build_id=prev_deps[0].build_id)
                create_dependency_changes_time.stop()
            if len(resolved_map) > util.config['dependency']['persist_chunk_size']:
                persist()
                resolved_map = {}
                problems = []
                changes = []
            generate_dependency_changes_time.stop()
        persist()

    def run(self, collection, repo_id):
        """
        Generates new dependency changes for requested repo using given
        collection. Finishes early when base buildroot is not resolvable.
        Updates collection resolution metadata (repo_id, base_resolved) after
        finished. Commits data in increments.
        """
        total_time.reset()
        generate_dependency_changes_time.reset()
        fetch_dependencies_generator_time.reset()
        total_time.start()
        self.log.info("Generating new repo")
        repo_descriptor = RepoDescriptor(repo_id=repo_id, koji_id='primary',
                                         build_tag=None)
        self.set_descriptor_tags([repo_descriptor])
        if not repo_descriptor.build_tag:
            self.log.error('Cannot generate repo: {}'.format(repo_id))
            self.db.rollback()
            return
        self.repo_cache.prefetch_repo(repo_descriptor)
        packages = self.get_packages(collection, require_build=True)
        brs = util.get_rpm_requires(self.koji_sessions['secondary'],
                                    [p.srpm_nvra for p in packages])
        brs = util.parallel_generator(brs, queue_size=None)
        try:
            with self.repo_cache.get_sack(repo_descriptor) as sack:
                if not sack:
                    self.log.error('Cannot generate repo: {}'.format(repo_id))
                    self.db.rollback()
                    return
                resolved, base_problems, _ = self.resolve_dependencies(sack, [])
                resolution_time.stop()
                if not resolved:
                    self.log.info("Build group not resolvable for {}"
                                  .format(collection.name))
                    collection.latest_repo_id = repo_id
                    collection.latest_repo_resolved = False
                    self.db.execute(BuildrootProblem.__table__.insert(),
                                    [{'collection_id': collection.id, 'problem': problem}
                                     for problem in base_problems])
                    self.db.commit()
                    return
                self.log.info("Resolving dependencies...")
                resolution_time.start()
                self.generate_dependency_changes(sack, packages, brs, repo_id)
                resolution_time.stop()
        finally:
            brs.stop()
        collection.latest_repo_id = repo_id
        collection.latest_repo_resolved = True
        self.db.commit()
        total_time.stop()
        total_time.display()
        generate_dependency_changes_time.display()
        fetch_dependencies_generator_time.display()


class ProcessBuildsTask(AbstractResolverTask):

    def repo_descriptor_for_build(self, build):
        return RepoDescriptor('primary',
                              None, build.repo_id)

    def process_build(self, sack, entry, curr_deps):
        self.log.info("Processing build {}".format(entry.id))
        prev = self.get_prev_build_for_comparison(entry)
        self.store_deps(entry.id, curr_deps)
        self.db.query(Build).filter_by(id=entry.id)\
            .update({'deps_processed': True, 'deps_resolved': curr_deps is not None})
        if curr_deps is None and entry.id == entry.last_build_id:
            failed_prio = 3 * util.config['priorities']['failed_build_priority']
            self.db.query(Package).filter_by(id=entry.package_id)\
                .update({'manual_priority': Package.manual_priority + failed_prio})
        if curr_deps is None:
            return
        if prev:
            prev_deps = prev.dependencies
            if prev_deps and curr_deps:
                changes = self.create_dependency_changes(prev_deps, curr_deps,
                                                         build_id=entry.id,
                                                         prev_build_id=prev.id)
                if changes:
                    self.db.execute(AppliedChange.__table__.insert(), changes)
        old_builds = self.db.query(Build.id)\
            .filter_by(package_id=entry.package_id)\
            .filter(Build.id < entry.id)\
            .order_by(Build.id.desc())\
            .subquery()
        self.db.query(Dependency)\
               .filter(Dependency.build_id.in_(old_builds))\
               .delete(synchronize_session=False)

    def run(self):
        # pylint: disable=E1101
        builds = self.db.query(Build.id, Build.repo_id, Build.real, Build.package_id,
                               Package.name, Build.version, Build.release,
                               Package.last_build_id)\
            .join(Build.package)\
            .filter(Build.deps_processed == False)\
            .filter(Build.repo_id != None)\
            .order_by(Build.repo_id).all()

        descriptors = [self.repo_descriptor_for_build(build) for build in builds]
        self.set_descriptor_tags(descriptors)
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
                .update({'deps_processed': True}, synchronize_session=False)
            self.db.commit()
        for descriptor, _ in groupby(repos_to_process, lambda desc: desc):
            self.repo_cache.prefetch_repo(descriptor)
        buildrequires = util.get_rpm_requires(self.koji_sessions['secondary'],
                                              [dict(name=b.name, version=b.version,
                                                    release=b.release, arch='src')
                                               for b in builds_to_process])
        if len(builds) > 100:
            buildrequires = util.parallel_generator(buildrequires, queue_size=None)
        for repo_descriptor, group in groupby(izip(repos_to_process,
                                                   builds_to_process,
                                                   buildrequires),
                                              lambda item: item[0]):
            with self.repo_cache.get_sack(repo_descriptor) as sack:
                if sack:
                    for _, build, brs in group:
                        _, _, curr_deps = self.resolve_dependencies(sack, brs)
                        self.process_build(sack, build, curr_deps)
                        self.db.commit()
                else:
                    self.log.info("Repo id=%d not available, skipping",
                                  repo_descriptor.repo_id)
            sack = None
        self.db.query(Build)\
            .filter_by(repo_id=None)\
            .filter(Build.state.in_(Build.FINISHED_STATES))\
            .update({'deps_processed': True}, synchronize_session=False)
        self.db.commit()


class Resolver(KojiService):

    def __init__(self, log=None, db=None, koji_sessions=None,
                 repo_cache=None):
        super(Resolver, self).__init__(log=log, db=db,
                                       koji_sessions=koji_sessions)
        self.repo_cache = repo_cache or RepoCache()

    def create_task(self, cls):
        return cls(log=self.log, db=self.db, koji_sessions=self.koji_sessions,
                   repo_cache=self.repo_cache)

    def main(self):
        self.create_task(ProcessBuildsTask).run()
        for collection in self.db.query(Collection).all():
            curr_repo = util.get_latest_repo(self.koji_sessions['primary'],
                                             collection.build_tag)
            if curr_repo and curr_repo['id'] > collection.latest_repo_id:
                self.create_task(GenerateRepoTask).run(collection, curr_repo['id'])
