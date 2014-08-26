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

from sqlalchemy import except_, or_, intersect

from koschei.models import Package, Dependency, DependencyChange, Repo, \
                           ResolutionResult, ResolutionProblem, RepoGenerationRequest
from koschei import util
from koschei.service import KojiService
from koschei.srpm_cache import SRPMCache
from koschei.repo_cache import RepoCache

def get_srpm_pkg(sack, name):
    hawk_pkg = hawkey.Query(sack).filter(name=name, arch='src',
                                         latest_per_arch=True)
    if hawk_pkg:
        return hawk_pkg[0]

class Resolver(KojiService):
    def __init__(self, log=None, db_session=None, koji_session=None, srpm_cache=None, repo_cache=None):
        super(Resolver, self).__init__(log=log, db_session=db_session, koji_session=koji_session)
        self.srpm_cache = srpm_cache or SRPMCache(koji_session=self.koji_session)
        self.repo_cache = repo_cache or RepoCache()

    def set_resolved(self, repo, package):
        result = ResolutionResult(package_id=package.id, resolved=True, repo_id=repo.id)
        self.db_session.add(result)
        package.state = Package.OK
        self.db_session.add(package)
        self.db_session.flush()

    def set_unresolved(self, repo, package, problems):
        result = ResolutionResult(package_id=package.id, resolved=False, repo_id=repo.id)
        self.db_session.add(result)
        self.db_session.flush()
        for problem in problems:
            entry = ResolutionProblem(resolution_id=result.id, problem=problem)
            self.db_session.add(entry)
        package.state = Package.UNRESOLVED
        self.db_session.add(package)
        self.db_session.flush()

    def prepare_goal(self, sack, srpm, group):
        goal = hawkey.Goal(sack)
        for name in group:
            sltr = hawkey.Selector(sack).set(name=name)
            goal.install(select=sltr)
        goal.install(srpm)
        return goal

    def create_dependencies(self, repo, package, installs):
        new_deps = []
        for install in installs:
            if install.arch != 'src':
                dep = Dependency(repo_id=repo.id, package_id=package.id,
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
        dep_map = {dep.name for dep in deps}
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

    def resolve_dependencies(self, sack, repo, package, group):
        srpm = get_srpm_pkg(sack, package.name)
        if srpm:
            goal = self.prepare_goal(sack, srpm, group)
            if goal.run():
                self.set_resolved(repo, package)
                # pylint: disable=E1101
                installs = goal.list_installs()
                deps = self.create_deps(repo, package, installs)
                self.set_dependency_distances(sack, srpm, deps)
                self.store_dependencies(deps)
            else:
                self.set_unresolved(repo, package, goal.problems)

    def compute_dependency_differences(self, package_id, repo_id1, repo_id2, apply_id):
        s = self.db_session
        if (s.query(ResolutionResult).filter_by(package_id=package_id, resolved=True)
             .filter(ResolutionResult.repo_id.in_([repo_id1, repo_id2]))
             .count() == 2):
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
        assert current_repo.id is not None
        self.db_session.query(Dependency).filter(Dependency.repo_id != current_repo.id)\
                       .delete()
        self.db_session.commit()

    def generate_repo(self, repo_id):
        packages = self.db_session.query(Package)\
                             .filter(or_(Package.state == Package.OK,
                                         Package.state == Package.UNRESOLVED))
        package_names = [pkg.name for pkg in packages]
        self.log.info("Generating new repo")
        for_arch = util.config['dependency']['for_arch']
        self.srpm_cache.get_latest_srpms(package_names)
        self.srpm_cache.createrepo()
        sack = hawkey.Sack(arch=for_arch)
        repos = self.repo_cache.get_repos(repo_id)
        util.add_repos_to_sack(repo_id, repos, sack)
        #TODO repo_id
        group = util.get_build_group()
        db_repo = Repo(repo_id=repo_id)
        self.db_session.add(db_repo)
        self.db_session.flush()
        self.log.info("Resolving dependencies")
        for pkg in packages:
            self.resolve_dependencies(sack, db_repo, pkg, group)
        self.log.info("Computing dependency differences")
        self.process_dependency_differences()
        self.log.info("Computing dependency distances")
        for pkg in packages:
            self.compute_dependency_distance(sack, pkg)
        self.db_session.commit()
        self.cleanup_deps(db_repo)
        self.log.info("New repo done")

    def main(self):
        request_query = self.db_session.query(RepoGenerationRequest)\
                                       .order_by(RepoGenerationRequest.repo_id.desc())
        latest_request = request_query.first()
        if latest_request:
            repo_id = latest_request.repo_id
            repo = self.db_session.query(Repo).filter_by(repo_id=repo_id).first()
            if not repo:
                self.generate_repo(repo_id)
