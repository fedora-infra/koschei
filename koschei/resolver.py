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

import time
import hawkey
import itertools

from sqlalchemy.orm import joinedload

from koschei.models import Package, Dependency, DependencyChange, \
                           ResolutionResult, ResolutionProblem, RepoGenerationRequest, \
                           Build
from koschei import util
from koschei.service import KojiService
from koschei.srpm_cache import SRPMCache
from koschei.repo_cache import RepoCache
from koschei.backend import check_package_state

def get_srpm_pkg(sack, name, evr=None):
    if evr:
        # pylint: disable=W0633
        epoch, version, release = evr
        hawk_pkg = hawkey.Query(sack).filter(name=name, epoch=epoch or 0, arch='src',
                                             version=version, release=release)
    else:
        hawk_pkg = hawkey.Query(sack).filter(name=name, arch='src',
                                             latest_per_arch=True)
    if hawk_pkg:
        return hawk_pkg[0]

class Resolver(KojiService):
    def __init__(self, log=None, db_session=None, koji_session=None, srpm_cache=None, repo_cache=None):
        super(Resolver, self).__init__(log=log, db_session=db_session, koji_session=koji_session)
        self.srpm_cache = srpm_cache or SRPMCache(koji_session=self.koji_session)
        self.repo_cache = repo_cache or RepoCache()

    def prepare_goal(self, sack, srpm, group):
        goal = hawkey.Goal(sack)
        for name in group:
            sltr = hawkey.Selector(sack).set(name=name)
            goal.install(select=sltr)
        goal.install(srpm)
        return goal

    def store_deps(self, repo_id, package_id, installs):
        new_deps = []
        for install in installs or []:
            if install.arch != 'src':
                dep = Dependency(repo_id=repo_id, package_id=package_id,
                                 name=install.name, epoch=install.epoch,
                                 version=install.version, release=install.release,
                                 arch=install.arch)
                new_deps.append(dep)

        if new_deps:
            # pylint: disable=E1101
            table = Dependency.__table__
            dicts = [{c.name: getattr(dep, c.name) for c in table.c if not c.primary_key}
                     for dep in new_deps]
            self.db_session.connection().execute(table.insert(), dicts)
            self.db_session.expire_all()

    def compute_dependency_distances(self, sack, srpm, deps):
        dep_map = {dep.name: dep for dep in deps}
        visited = set()
        level = 1
        reldeps = srpm.requires
        while level < 5 and reldeps:
            pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))
            reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                               for req in pkg.requires}
            visited.update(pkgs_on_level)
            for pkg in pkgs_on_level:
                dep = dep_map.get(pkg.name)
                if dep and dep.distance is None:
                    dep.distance = level
            level += 1

    def resolve_dependencies(self, sack, package, srpm, group, repo_id):
        goal = self.prepare_goal(sack, srpm, group)
        if goal is not None:
            resolved = goal.run()
            result = ResolutionResult(repo_id=repo_id, package_id=package.id,
                                      resolved=resolved)
            self.db_session.add(result)
            self.db_session.flush()
            if resolved:
                # pylint: disable=E1101
                deps = [Dependency(name=pkg.name, epoch=pkg.epoch,
                                   version=pkg.version, release=pkg.release,
                                   arch=pkg.arch)
                        for pkg in goal.list_installs() if pkg.arch != 'src']
                self.compute_dependency_distances(sack, srpm, deps)
                return deps
            else:
                for problem in goal.problems:
                    entry = ResolutionProblem(resolution_id=result.id, problem=problem)
                    self.db_session.add(entry)

    def get_deps_from_db(self, package_id, repo_id):
        deps = self.db_session.query(Dependency)\
                              .filter_by(repo_id=repo_id, package_id=package_id)
        return deps.all()

    def generate_dependency_differences(self, deps1, deps2, package_id,
                                        apply_id=None):
        if not deps1 or not deps2:
            # TODO packages with no deps
            return
        def key(dep):
            return (dep.name, dep.epoch, dep.version, dep.release)
        old = util.set_difference(deps1, deps2, key)
        new = util.set_difference(deps2, deps1, key)
        def create_change(name):
            return dict(package_id=package_id, applied_in_id=apply_id, dep_name=name,
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
        if changes:
            # pylint: disable=E1101
            self.db_session.execute(DependencyChange.__table__.insert(), changes.values())
            self.db_session.expire_all()

    def prepare_sack(self, repo_id):
        for_arch = util.config['dependency']['for_arch']
        sack = hawkey.Sack(arch=for_arch)
        repos = self.repo_cache.get_repos(repo_id)
        if repos:
            util.add_repos_to_sack(repo_id, repos, sack)
            return sack

    def get_packages(self):
        return self.db_session.query(Package)\
                              .filter(Package.ignored == False)\
                              .options(joinedload(Package.all_builds))\
                              .all()

    def generate_repo(self, repo_id):
        start = time.time()
        self.db_session.query(DependencyChange)\
                       .filter_by(applied_in_id=None)\
                       .delete(synchronize_session=False)
        packages = self.get_packages()
        package_names = [pkg.name for pkg in packages]
        prev_states = {pkg.id: pkg.state_string for pkg in packages}
        self.log.info("Generating new repo")
        self.srpm_cache.get_latest_srpms(package_names)
        srpm_repo = self.srpm_cache.get_repodata()
        sack = self.prepare_sack(repo_id)
        util.add_repo_to_sack('src', srpm_repo, sack)
        #TODO repo_id
        group = util.get_build_group()
        self.log.info("Resolving dependencies")
        resolution_start = time.time()
        for package in packages:
            srpm = get_srpm_pkg(sack, package.name)
            curr_deps = self.resolve_dependencies(sack, package, srpm, group, repo_id)
            if curr_deps is not None:
                last_build = package.all_builds[0]
                if last_build and last_build.repo_id:
                    prev_deps = self.get_deps_from_db(last_build.package_id,
                                                      last_build.repo_id)
                    if prev_deps is not None:
                        self.generate_dependency_differences(prev_deps, curr_deps,
                                                             package.id)
        packages = self.get_packages()
        for pkg in packages:
            prev_state = prev_states[pkg.id]
            check_package_state(pkg, prev_state)

        self.db_session.commit()
        end = time.time()

        self.log.info(("New repo done. Resolution time: {} minutes\n"
                       "Overall time: {} minutes.")
                      .format((end - resolution_start) / 60, (end - start) / 60))

    def process_repo_generation_requests(self):
        latest_request = self.db_session.query(RepoGenerationRequest)\
                                        .order_by(RepoGenerationRequest.repo_id.desc())\
                                        .first()
        if latest_request:
            repo_id = latest_request.repo_id
            if not self.db_session.query(ResolutionResult)\
                                  .filter_by(repo_id=repo_id).first():
                self.generate_repo(repo_id)
            self.db_session.query(RepoGenerationRequest)\
                           .filter(RepoGenerationRequest.repo_id <= repo_id)\
                           .delete()
            self.db_session.commit()

    def get_prev_build(self, build):
        return self.db_session.query(Build).filter_by(package_id=build.package_id)\
                              .filter(Build.id < build.id)\
                              .order_by(Build.id.desc()).first()

    def process_build(self, build, sack, build_group):
        build.deps_processed = True
        if build.repo_id:
            self.log.info("Processing build {}".format(build.id))
            prev = self.get_prev_build(build)
            srpm = get_srpm_pkg(sack, build.package.name, (build.epoch, build.version,
                                                           build.release))
            curr_deps = self.resolve_dependencies(sack, package=build.package,
                                                  srpm=srpm, group=build_group,
                                                  repo_id=build.repo_id)
            self.store_deps(build.repo_id, build.package_id, curr_deps)
            if prev and prev.repo_id:
                prev_deps = self.get_deps_from_db(prev.package_id, prev.repo_id)
                if prev_deps and curr_deps:
                    self.db_session.query(DependencyChange)\
                                   .filter_by(applied_in_id=build.id)\
                                   .delete(synchronize_session=False)
                self.generate_dependency_differences(prev_deps, curr_deps,
                                                     package_id=build.package_id,
                                                     apply_id=build.id)
                self.db_session.query(Dependency)\
                               .filter_by(package_id=build.package_id)\
                               .filter(Dependency.repo_id < build.repo_id)\
                               .delete(synchronize_session=False)

    def process_builds(self):
        # pylint: disable=E1101
        unprocessed = self.db_session.query(Build).filter_by(deps_processed=False)\
                                     .filter(Build.repo_id != None)\
                                     .options(joinedload(Package.all_builds))\
                                     .order_by(Build.repo_id).all()
        # TODO repo_id
        group = util.get_build_group()

        # do this before processing to avoid multiple runs of createrepo
        for build in unprocessed:
            self.srpm_cache.get_srpm(build.package.name, build.epoch, build.version,
                                     build.release)
        srpm_repo = self.srpm_cache.get_repodata()

        for repo_id, builds in itertools.groupby(unprocessed,
                                                 lambda build: build.repo_id):
            if repo_id is not None:
                sack = self.prepare_sack(repo_id)
                if sack:
                    util.add_repos_to_sack('srpm', {'src': srpm_repo}, sack)
                    for build in builds:
                        self.process_build(build, sack, group)
            self.db_session.commit()

    def main(self):
        self.process_builds()
        self.process_repo_generation_requests()
