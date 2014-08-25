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

    def resolve_dependencies(self, sack, repo, package, group):
        new_deps = []
        hawk_pkg = get_srpm_pkg(sack, package.name)
        if not hawk_pkg:
            return
        goal = hawkey.Goal(sack)
        for name in group:
            sltr = hawkey.Selector(sack).set(name=name)
            goal.install(select=sltr)
        goal.install(hawk_pkg)
        if goal.run():
            self.set_resolved(repo, package)
            # pylint: disable=E1101
            installs = goal.list_installs()
            for install in installs:
                if install.arch != 'src':
                    dep = Dependency(repo_id=repo.id, package_id=package.id,
                                     name=install.name, epoch=install.epoch,
                                     version=install.version, release=install.release,
                                     arch=install.arch)
                    new_deps.append(dep)
        else:
            self.set_unresolved(repo, package, goal.problems)

        if new_deps:
            # pylint: disable=E1101
            table = Dependency.__table__
            dicts = [{c.name: getattr(dep, c.name) for c in table.c if not c.primary_key}
                     for dep in new_deps]
            self.db_session.connection().execute(table.insert(), dicts)
            self.db_session.expire_all()

    def get_dependency_differences(self):
        def difference_query(*repos):
            resolved = intersect(*(self.db_session.query(ResolutionResult.package_id)\
                                       .filter_by(resolved=True, repo_id=r) for r in repos))
            deps = (self.db_session.query(Dependency.package_id, *Dependency.nevra)
                                   .filter(Dependency.repo_id == r)
                                   .filter(Dependency.package_id.in_(resolved))
                        for r in repos)
            return self.db_session.connection().execute(except_(*deps))
        last_repos = self.db_session.query(Repo.id).order_by(Repo.id.desc()).limit(2).all()
        if len(last_repos) != 2:
            return [], []
        [curr_repo], [prev_repo] = last_repos
        add_diff = difference_query(curr_repo, prev_repo)
        rm_diff = difference_query(prev_repo, curr_repo)
        return add_diff, rm_diff

    def process_dependency_differences(self):
        add_diff, rm_diff = self.get_dependency_differences()
        changes = {}
        for pkg_id, dep_name, epoch, version, release, arch in add_diff:
            change = DependencyChange(package_id=pkg_id, dep_name=dep_name,
                                      curr_epoch=epoch, curr_version=version,
                                      curr_release=release)
            changes[(pkg_id, dep_name, arch)] = change
        for pkg_id, dep_name, epoch, version, release, arch in rm_diff:
            update = changes.get((pkg_id, dep_name, arch))
            if update:
                update.prev_epoch = epoch
                update.prev_version = version
                update.prev_release = release
            else:
                change = DependencyChange(package_id=pkg_id, dep_name=dep_name,
                                          curr_epoch=epoch, curr_version=version,
                                          curr_release=release)
                changes[(pkg_id, dep_name, arch)] = change
        for change in changes.values():
            self.db_session.add(change)
        self.db_session.flush()

    def compute_dependency_distance(self, sack, package):
        hawk_pkg = get_srpm_pkg(sack, package.name)
        if not hawk_pkg:
            return
        changes = self.db_session.query(DependencyChange)\
                                 .filter(DependencyChange.package_id == package.id,
                                         DependencyChange.applied_in_id == None,
                                         DependencyChange.curr_version != None).all()
        if not changes:
            return
        changes_map = {change.dep_name: change for change in changes}
        visited = set()
        level = 1
        reldeps = hawk_pkg.requires
        while level < 8 and reldeps:
            pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))
            reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                               for req in pkg.requires}
            visited.update(pkgs_on_level)
            for pkg in pkgs_on_level:
                if pkg.name in changes_map and not changes_map[pkg.name].distance:
                    changes_map[pkg.name].distance = level
            level += 1
        self.db_session.flush()

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
