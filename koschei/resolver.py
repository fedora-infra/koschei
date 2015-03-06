# Copyright (C) 2014  Red Hat, Inc.
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

import os
import time
import hawkey
import itertools
import dnf.subject
import dnf.sack

from sqlalchemy.orm import joinedload

from koschei.models import (Package, Dependency, DependencyChange, Repo,
                            ResolutionProblem, RepoGenerationRequest, Build)
from koschei import util
from koschei.service import KojiService
from koschei.srpm_cache import SRPMCache
from koschei.repo_cache import RepoCache
from koschei.backend import check_package_state, Backend
from koschei.util import itercall


class AbstractResolverTask(object):
    def __init__(self, log, db, koji_session,
                 srpm_cache, repo_cache, backend):
        self.log = log
        self.db = db
        self.koji_session = koji_session
        self.srpm_cache = srpm_cache
        self.repo_cache = repo_cache
        self.backend = backend
        self.problems = []
        self.sack = None
        self.group = None
        self.resolved_packages = {}

    def get_srpm_pkg(self, name, evr=None):
        if evr:
            # pylint: disable=W0633
            epoch, version, release = evr
            hawk_pkg = hawkey.Query(self.sack)\
                             .filter(name=name, epoch=epoch or 0,
                                     arch='src', version=version,
                                     release=release)
        else:
            hawk_pkg = hawkey.Query(self.sack)\
                             .filter(name=name, arch='src',
                                     latest_per_arch=True)
        if hawk_pkg:
            return hawk_pkg[0]

    def prepare_goal(self, srpm):
        goal = hawkey.Goal(self.sack)
        problems = []
        for name in self.group:
            sltr = hawkey.Selector(self.sack).set(name=name)
            goal.install(select=sltr)
        for reldep in srpm.requires:
            subj = dnf.subject.Subject(str(reldep))
            sltr = subj.get_best_selector(self.sack)
            # pylint: disable=E1103
            if sltr is None or not sltr.matches():
                problems.append("No package found for: {}".format(reldep))
            else:
                goal.install(select=sltr)
        return goal, problems

    def store_deps(self, repo_id, package_id, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = Dependency(repo_id=repo_id, package_id=package_id,
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

    def compute_dependency_distances(self, srpm, deps):
        dep_map = {dep.name: dep for dep in deps}
        visited = set()
        level = 1
        reldeps = srpm.requires
        while level < 5 and reldeps:
            pkgs_on_level = set(hawkey.Query(self.sack).filter(provides=reldeps))
            reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                       for req in pkg.requires}
            visited.update(pkgs_on_level)
            for pkg in pkgs_on_level:
                dep = dep_map.get(pkg.name)
                if dep and dep.distance is None:
                    dep.distance = level
            level += 1

    def resolve_dependencies(self, package, srpm, repo_id):
        goal, problems = self.prepare_goal(srpm)
        if goal is not None:
            resolved = False
            if not problems:
                resolved = goal.run()
                problems = goal.problems
            self.resolved_packages[package.id] = resolved
            if resolved:
                # pylint: disable=E1101
                deps = [Dependency(name=pkg.name, epoch=pkg.epoch,
                                   version=pkg.version, release=pkg.release,
                                   arch=pkg.arch)
                        for pkg in goal.list_installs() if pkg.arch != 'src']
                self.compute_dependency_distances(srpm, deps)
                return deps
            else:
                for problem in sorted(set(problems)):
                    entry = dict(package_id=package.id,
                                 problem=problem)
                    self.problems.append(entry)

    def get_deps_from_db(self, package_id, repo_id):
        deps = self.db.query(Dependency)\
                      .filter_by(repo_id=repo_id,
                                 package_id=package_id)
        return deps.all()

    def create_dependency_changes(self, deps1, deps2, package_id,
                                  apply_id=None):
        if not deps1 or not deps2:
            # TODO packages with no deps
            return []

        def key(dep):
            return (dep.name, dep.epoch, dep.version, dep.release)

        old = util.set_difference(deps1, deps2, key)
        new = util.set_difference(deps2, deps1, key)

        def create_change(name):
            return dict(package_id=package_id, applied_in_id=apply_id,
                        dep_name=name,
                        prev_epoch=None, prev_version=None, prev_release=None,
                        curr_epoch=None, curr_version=None, curr_release=None)

        changes = {}
        for dep in old:
            change = create_change(dep.name)
            change.update(prev_version=dep.version, prev_epoch=dep.epoch,
                          prev_release=dep.release, distance=dep.distance)
            changes[dep.name] = change
        for dep in new:
            change = changes.get(dep.name) or create_change(dep.name)
            change.update(curr_version=dep.version, curr_epoch=dep.epoch,
                          curr_release=dep.release, distance=dep.distance)
            changes[dep.name] = change
        return changes.values() if changes else []

    def update_dependency_changes(self, changes, apply_id=None):
        # pylint: disable=E1101
        self.db.query(DependencyChange)\
               .filter_by(applied_in_id=apply_id)\
               .delete(synchronize_session=False)
        if changes:
            self.db.execute(DependencyChange.__table__.insert(),
                            changes)
        self.db.expire_all()

    def get_prev_build_with_repo_id(self, build):
        return self.db.query(Build)\
                      .filter_by(package_id=build.package_id)\
                      .filter(Build.task_id < build.task_id)\
                      .filter(Build.repo_id != None)\
                      .order_by(Build.task_id.desc()).first()

    def prepare_sack(self, repo_id):
        for_arch = util.config['dependency']['for_arch']
        sack = dnf.sack.Sack(arch=for_arch)
        repos = self.repo_cache.get_repos(repo_id)
        if repos:
            util.add_repos_to_sack(repo_id, repos, sack)
            self.sack = sack


class GenerateRepoTask(AbstractResolverTask):

    def get_packages(self, expunge=True):
        packages = self.db.query(Package)\
                          .filter(Package.ignored == False)\
                          .options(joinedload(Package.last_build))\
                          .options(joinedload(Package.last_complete_build))\
                          .all()
        # detaches objects from ORM, prevents spurious queries that hinder
        # performance
        if expunge:
            self.db.expunge_all()
        return packages

    def get_latest_task_infos(self, packages):
        source_tag = util.koji_config['source_tag']
        infos = itercall(self.koji_session, packages,
                         lambda k, p: k.listTagged(source_tag, latest=True,
                                                   package=p.name))
        return {pkg: i[0] for pkg, i in zip(packages, infos) if i}

    def refresh_latest_builds(self, packages):
        """
        Checks Koji for latest builds of all packages and registers possible
        new real builds and obtains srpms for them
        """
        task_infos = self.get_latest_task_infos(packages)
        self.backend.register_real_builds(task_infos)
        self.srpm_cache.get_latest_srpms([i for (_, i) in task_infos.items()])

    def update_repo_index(self, repo_id):
        index_path = os.path.join(util.config['directories']['repodata'], 'index')
        with open(index_path, 'w') as index:
            index.write('{}\n'.format(repo_id))

    def synchronize_resolution_state(self):
        packages = self.get_packages(expunge=False)
        for pkg in packages:
            curr_state = self.resolved_packages.get(pkg.id)
            if curr_state is not None:
                prev_state = pkg.state_string
                pkg.resolved = curr_state
                check_package_state(pkg, prev_state)

        for state in True, False:
            ids = [id for id, resolved in self.resolved_packages.iteritems()
                   if resolved is state]
            if ids:
                self.db.query(Package).filter(Package.id.in_(ids))\
                    .update({'resolved': state}, synchronize_session=False)

    def get_build_for_comparison(self, package):
        """
        Returns newest build which should be used for dependency
        comparisons or None if it shouldn't be compared at all
        """
        last_build = package.last_build
        if last_build:
            if last_build.repo_id:
                return last_build
            if last_build.state in Build.FINISHED_STATES:
                return self.get_prev_build_with_repo_id(last_build)

    def generate_dependency_changes(self, packages, repo_id):
        changes = []
        for package in packages:
            srpm = self.get_srpm_pkg(package.name)
            if not srpm:
                continue
            curr_deps = self.resolve_dependencies(package, srpm, repo_id)
            if curr_deps is not None:
                last_build = self.get_build_for_comparison(package)
                if last_build:
                    prev_deps = self.get_deps_from_db(last_build.package_id,
                                                      last_build.repo_id)
                    if prev_deps is not None:
                        changes += self.create_dependency_changes(prev_deps,
                                                                  curr_deps,
                                                                  package.id)
        return changes

    def run(self, repo_id):
        start = time.time()
        self.log.info("Generating new repo")
        self.db.add(Repo(repo_id=repo_id))
        self.db.flush()
        packages = self.get_packages()
        self.refresh_latest_builds(packages)
        packages = self.get_packages()
        srpm_repo = self.srpm_cache.get_repodata()
        self.prepare_sack(repo_id)
        if not self.sack:
            self.log.error('Cannot generate repo: {}'.format(repo_id))
            return
        self.update_repo_index(repo_id)
        util.add_repo_to_sack('src', srpm_repo, self.sack)
        # TODO repo_id
        self.group = util.get_build_group()
        self.log.info("Resolving dependencies")
        resolution_start = time.time()
        changes = self.generate_dependency_changes(packages, repo_id)
        resolution_end = time.time()
        self.db.query(ResolutionProblem).delete(synchronize_session=False)
        # pylint: disable=E1101
        if self.problems:
            self.db.execute(ResolutionProblem.__table__.insert(), self.problems)
        self.synchronize_resolution_state()
        self.update_dependency_changes(changes)
        self.db.commit()
        end = time.time()

        self.log.info(("New repo done. Resolution time: {} minutes\n"
                       "Overall time: {} minutes.")
                      .format((resolution_end - resolution_start) / 60,
                              (end - start) / 60))

class ProcessBuildsTask(AbstractResolverTask):

    def process_build(self, build):
        if build.repo_id:
            self.log.info("Processing build {}".format(build.id))
            prev = self.get_prev_build_with_repo_id(build)
            srpm = self.get_srpm_pkg(build.package.name, (build.epoch,
                                                          build.version,
                                                          build.release))
            curr_deps = self.resolve_dependencies(package=build.package,
                                                  srpm=srpm,
                                                  repo_id=build.repo_id)
            self.store_deps(build.repo_id, build.package_id, curr_deps)
            if curr_deps is not None:
                build.deps_resolved = True
            if prev and prev.repo_id:
                prev_deps = self.get_deps_from_db(prev.package_id,
                                                  prev.repo_id)
                if prev_deps and curr_deps:
                    changes = self.create_dependency_changes(
                        prev_deps, curr_deps, package_id=build.package_id,
                        apply_id=build.id)
                    self.update_dependency_changes(changes, apply_id=build.id)
                keep_builds = util.config['dependency']['keep_build_deps_for']
                boundary_build = self.db.query(Build)\
                                     .filter_by(package_id=build.package_id)\
                                     .order_by(Build.task_id.desc())\
                                     .offset(keep_builds).first()
                if boundary_build and boundary_build.repo_id:
                    self.db.query(Dependency)\
                           .filter_by(package_id=build.package_id)\
                           .filter(Dependency.repo_id <
                                   boundary_build.repo_id)\
                           .delete(synchronize_session=False)

    def run(self):
        # pylint: disable=E1101
        unprocessed = self.db.query(Build)\
                             .filter_by(deps_processed=False)\
                             .filter(Build.repo_id != None)\
                             .options(joinedload(Build.package))\
                             .order_by(Build.repo_id).all()
        # TODO repo_id
        self.group = util.get_build_group()

        # do this before processing to avoid multiple runs of createrepo
        for build in unprocessed:
            self.srpm_cache.get_srpm(build.package.name, build.epoch,
                                     build.version, build.release)
        srpm_repo = self.srpm_cache.get_repodata()

        for repo_id, builds in itertools.groupby(unprocessed,
                                                 lambda build: build.repo_id):
            builds = list(builds)
            if repo_id is not None:
                self.prepare_sack(repo_id)
                if self.sack:
                    util.add_repos_to_sack('srpm', {'src': srpm_repo}, self.sack)
                    for build in builds:
                        self.process_build(build)
            self.db.query(Build).filter(Build.id.in_([b.id for b in builds]))\
                                .update({'deps_processed': True},
                                        synchronize_session=False)
            self.db.commit()

class Resolver(KojiService):

    def __init__(self, log=None, db=None, koji_session=None,
                 srpm_cache=None, repo_cache=None, backend=None):
        super(Resolver, self).__init__(log=log, db=db,
                                       koji_session=koji_session)
        self.srpm_cache = (srpm_cache
                           or SRPMCache(koji_session=self.koji_session))
        self.repo_cache = repo_cache or RepoCache()
        self.backend = backend or Backend(db=self.db,
                                          koji_session=self.koji_session,
                                          log=self.log)

    def create_task(self, cls):
        return cls(log=self.log, db=self.db, koji_session=self.koji_session,
                   srpm_cache=self.srpm_cache, repo_cache=self.repo_cache,
                   backend=self.backend)

    def process_repo_generation_requests(self):
        latest_request = self.db.query(RepoGenerationRequest)\
                                .order_by(RepoGenerationRequest.repo_id
                                          .desc())\
                                .first()
        if latest_request:
            repo_id = latest_request.repo_id
            [last_repo] = (self.db.query(Repo.repo_id)
                           .order_by(Repo.repo_id.desc())
                           .first() or [0])
            if repo_id > last_repo:
                self.create_task(GenerateRepoTask).run(repo_id)
            self.db.query(RepoGenerationRequest)\
                   .filter(RepoGenerationRequest.repo_id <= repo_id)\
                   .delete()
            self.db.commit()

    def main(self):
        self.create_task(ProcessBuildsTask).run()
        self.process_repo_generation_requests()
