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

import hawkey

from sqlalchemy import text, exists
from sqlalchemy.orm import joinedload

from koschei.models import Package, Dependency, DependencyChange, \
                           ResolutionResult, ResolutionProblem, RepoGenerationRequest, \
                           Build
from koschei.backend import watch_package_state
from koschei import util
from koschei.service import KojiService
from koschei.srpm_cache import SRPMCache
from koschei.repo_cache import RepoCache

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
        self.cached_sack = (None, None)

    def set_resolved(self, repo_id, package):
        with watch_package_state(package):
            result = ResolutionResult(package_id=package.id, resolved=True, repo_id=repo_id)
            self.db_session.add(result)
            self.db_session.flush()

    def set_unresolved(self, repo_id, package, problems):
        with watch_package_state(package):
            result = ResolutionResult(package_id=package.id, resolved=False, repo_id=repo_id)
            self.db_session.add(result)
            self.db_session.flush()
        for problem in problems:
            entry = ResolutionProblem(resolution_id=result.id, problem=problem)
            self.db_session.add(entry)
        self.db_session.flush()

    def prepare_goal(self, sack, srpm, group):
        goal = hawkey.Goal(sack)
        for name in group:
            sltr = hawkey.Selector(sack).set(name=name)
            goal.install(select=sltr)
        goal.install(srpm)
        return goal

    def create_dependencies(self, repo_id, package, installs):
        new_deps = []
        for install in installs:
            if install.arch != 'src':
                dep = Dependency(repo_id=repo_id, package_id=package.id,
                                 name=install.name, epoch=install.epoch,
                                 version=install.version, release=install.release,
                                 arch=install.arch)
                new_deps.append(dep)
        return new_deps

    def store_dependencies(self, deps):
        if deps:
            # pylint: disable=E1101
            table = Dependency.__table__
            dicts = [{c.name: getattr(dep, c.name) for c in table.c if not c.primary_key}
                     for dep in deps]
            self.db_session.connection().execute(table.insert(), dicts)
            self.db_session.expire_all()

    def set_dependency_distances(self, sack, srpm, deps):
        dep_map = {dep.name: dep for dep in deps}
        visited = set()
        level = 1
        reldeps = srpm.requires
        while level < 8 and reldeps:
            pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))
            reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                               for req in pkg.requires}
            visited.update(pkgs_on_level)
            for pkg in pkgs_on_level:
                dep = dep_map.get(pkg.name)
                if dep and dep.distance is None:
                    dep.distance = level
            level += 1

    def resolve_dependencies(self, sack, repo_id, package, group):
        srpm = get_srpm_pkg(sack, package.name)
        if srpm:
            goal = self.prepare_goal(sack, srpm, group)
            if goal.run():
                self.set_resolved(repo_id, package)
                # pylint: disable=E1101
                installs = goal.list_installs()
                deps = self.create_dependencies(repo_id, package, installs)
                self.set_dependency_distances(sack, srpm, deps)
                self.store_dependencies(deps)
            else:
                self.set_unresolved(repo_id, package, goal.problems)

    def compute_dependency_differences(self, package_id, repo_id1, repo_id2, apply_id=None):
        s = self.db_session
        if len(s.query(ResolutionResult.id)\
                .filter_by(package_id=package_id, resolved=True)\
                .filter(ResolutionResult.repo_id.in_([repo_id1, repo_id2])).all()) == 2:
            s.query(DependencyChange)\
             .filter_by(package_id=package_id, applied_in_id=apply_id)\
             .delete()
            s.flush()
            s.connection().execute(text("""
                INSERT INTO dependency_change (dep_name, package_id, applied_in_id,
                                               prev_epoch, prev_version, prev_release,
                                               curr_epoch, curr_version, curr_release,
                                               distance)
                SELECT deps1.name, deps1.package_id, :apply_id,
                       deps1.epoch, deps1.version, deps1.release,
                       deps2.epoch, deps2.version, deps2.release, deps2.distance
                FROM dependency AS deps1 INNER JOIN dependency AS deps2
                     ON deps1.name = deps2.name AND
                        (deps1.epoch != deps2.epoch OR
                         deps1.version != deps2.version OR
                         deps1.release != deps2.release)
                WHERE deps1.package_id = :package_id AND
                      deps2.package_id = :package_id AND
                      deps1.repo_id = :repo_id1 AND
                      deps2.repo_id = :repo_id2
            """), package_id=package_id, apply_id=apply_id,
                 repo_id1=repo_id1, repo_id2=repo_id2)

    def cleanup_deps(self, current_repo):
        subq = self.db_session.query(Build.package_id)\
                              .filter_by(repo_id=current_repo).subquery()
        self.db_session.query(Dependency)\
                       .filter(Dependency.repo_id == current_repo)\
                       .filter(Dependency.package_id.notin_(subq))\
               .delete(synchronize_session=False)

    def prepare_sack(self, repo_id):
        for_arch = util.config['dependency']['for_arch']
        sack = hawkey.Sack(arch=for_arch)
        repos = self.repo_cache.get_repos(repo_id)
        if repos:
            util.add_repos_to_sack(repo_id, repos, sack)
            return sack

    def generate_repo(self, repo_id):
        packages = self.db_session.query(Package)\
                                  .filter(Package.ignored == False)\
                                  .options(joinedload(Package.last_build)).all()
        package_names = [pkg.name for pkg in packages]
        self.log.info("Generating new repo")
        self.srpm_cache.get_latest_srpms(package_names)
        srpm_repo = self.srpm_cache.get_repodata()
        sack = self.prepare_sack(repo_id)
        util.add_repo_to_sack('src', srpm_repo, sack)
        #TODO repo_id
        group = util.get_build_group()
        self.log.info("Resolving dependencies")
        for pkg in packages:
            self.resolve_dependencies(sack, repo_id, pkg, group)
            if pkg.last_build.repo_id:
                self.compute_dependency_differences(pkg.id, pkg.last_build.repo_id, repo_id)
        self.db_session.commit()
        self.cleanup_deps(repo_id)
        self.log.info("New repo done")

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

    def resolve_deps_for_build(self, build, srpm, sack, group):
        if self.db_session.query(exists()\
                                 .where((Dependency.repo_id == build.repo_id) &
                                        (Dependency.package_id == build.package_id)))\
                          .scalar():
            # Already resolved
            return
        goal = self.prepare_goal(sack, srpm, group)
        if goal is not None:
            resolved = goal.run()
            result = ResolutionResult(repo_id=build.repo_id, package_id=build.package_id,
                                      resolved=resolved)
            self.db_session.add(result)
            self.db_session.flush()
            if resolved:
                # pylint: disable=E1101
                deps = self.create_dependencies(build.repo_id, build.package,
                                                goal.list_installs())
                self.store_dependencies(deps)
            else:
                for problem in goal.problems:
                    entry = ResolutionProblem(resolution_id=result.id, problem=problem)
                    self.db_session.add(entry)


    def compute_dependency_diff_for_build(self, build):
        prev = self.db_session.query(Build).filter_by(package_id=build.package_id)\
                              .filter(Build.id < build.id)\
                              .order_by(Build.id.desc()).first()
        if prev:
            if prev.repo_id:
                self.compute_dependency_differences(build.package_id, prev.repo_id,
                                                    build.repo_id, build.id)
            else:
                self.log.warn("Old build without repo_id - {}".format(prev.id))
                return

    def process_build(self, build, build_group):
        if build.repo_id:
            if self.cached_sack[0] == build.repo_id:
                sack = self.cached_sack[1]
            else:
                sack = self.prepare_sack(build.repo_id)
                self.cached_sack = (build.repo_id, sack)
            if sack:
                self.log.info("Processing build {}".format(build.id))
                self.srpm_cache.get_srpm(build.package.name, build.epoch, build.version,
                                         build.release)
                repo = self.srpm_cache.get_repodata()
                util.add_repos_to_sack('srpm', {'src': repo}, sack)
                srpm = get_srpm_pkg(sack, build.package.name, (build.epoch, build.version,
                                                               build.release))
                self.resolve_deps_for_build(build, srpm, sack, build_group)
                self.compute_dependency_diff_for_build(build)
                self.db_session.query(Dependency)\
                               .filter_by(package_id=build.package_id)\
                               .filter(Dependency.repo_id < build.repo_id)\
                               .delete(synchronize_session=False)
        build.deps_processed = True
        self.db_session.commit()

    def process_builds(self):
        unprocessed = self.db_session.query(Build).filter_by(deps_processed=False)\
                                     .filter(Build.repo_id != None)\
                                     .order_by(Build.id).all()
        # TODO repo_id
        group = util.get_build_group()
        for build in unprocessed:
            self.process_build(build, group)

    def main(self):
        self.process_builds()
        self.process_repo_generation_requests()
