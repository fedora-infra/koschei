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
import logging

from sqlalchemy import except_, or_, intersect

from koschei.models import Package, Dependency, DependencyChange, Repo, \
                           ResolutionResult, ResolutionProblem
from koschei import util

log = logging.getLogger('dependency')

def get_srpm_pkg(sack, name):
    hawk_pkg = hawkey.Query(sack).filter(name=name, arch='src',
                                         latest_per_arch=True)
    if hawk_pkg:
        return hawk_pkg[0]

def set_resolved(db_session, repo, package):
    result = ResolutionResult(package_id=package.id, resolved=True, repo_id=repo.id)
    db_session.add(result)
    package.state = Package.OK
    db_session.add(package)
    db_session.flush()

def set_unresolved(db_session, repo, package, problems):
    result = ResolutionResult(package_id=package.id, resolved=False, repo_id=repo.id)
    db_session.add(result)
    db_session.flush()
    for problem in problems:
        entry = ResolutionProblem(resolution_id=result.id, problem=problem)
        db_session.add(entry)
    package.state = Package.UNRESOLVED
    db_session.add(package)
    db_session.flush()

def resolve_dependencies(db_session, sack, repo, package, group):
    hawk_pkg = get_srpm_pkg(sack, package.name)
    if not hawk_pkg:
        return
    goal = hawkey.Goal(sack)
    for name in group:
        sltr = hawkey.Selector(sack).set(name=name)
        goal.install(select=sltr)
    goal.install(hawk_pkg)
    if goal.run():
        set_resolved(db_session, repo, package)
        # pylint: disable=E1101
        installs = goal.list_installs()
        for install in installs:
            if install.arch != 'src':
                dep = Dependency(repo_id=repo.id, package_id=package.id,
                                 name=install.name, epoch=install.epoch,
                                 version=install.version, release=install.release,
                                 arch=install.arch)
                db_session.add(dep)
    else:
        set_unresolved(db_session, repo, package, goal.problems)

def get_dependency_differences(db_session):
    def difference_query(*repos):
        resolved = intersect(*(db_session.query(Dependency.package_id)\
                               .filter(Dependency.repo_id == r) for r in repos))
        deps = (db_session.query(Dependency.package_id, *Dependency.nevra)
                          .filter(Dependency.repo_id == r)
                          .filter(Dependency.package_id.in_(resolved))
                    for r in repos)
        return db_session.get_bind().execute(except_(*deps))
    last_repos = db_session.query(Repo.id).order_by(Repo.id.desc()).limit(2).all()
    if len(last_repos) != 2:
        return [], []
    [curr_repo], [prev_repo] = last_repos
    add_diff = difference_query(curr_repo, prev_repo)
    rm_diff = difference_query(prev_repo, curr_repo)
    return add_diff, rm_diff

def process_dependency_differences(db_session):
    add_diff, rm_diff = get_dependency_differences(db_session)
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
        db_session.add(change)
    db_session.flush()

def compute_dependency_distance(db_session, sack, package):
    hawk_pkg = get_srpm_pkg(sack, package.name)
    if not hawk_pkg:
        return
    changes = db_session.query(DependencyChange)\
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
    db_session.flush()

def cleanup_deps(db_session, current_repo):
    assert current_repo.id is not None
    db_session.query(Dependency).filter(Dependency.repo_id != current_repo.id)\
              .delete()
    db_session.commit()

def repo_done(db_session):
    packages = db_session.query(Package)\
                         .filter(or_(Package.state == Package.OK,
                                     Package.state == Package.UNRESOLVED))
    package_names = [pkg.name for pkg in packages]
    log.info("Generating new repo")
    for_arch = util.config['dependency']['for_arch']
    _, repos = util.sync_repos(package_names)
    sack = util.create_sacks([for_arch], repos)[for_arch]
    group = util.get_build_group()
    db_repo = Repo()
    db_session.add(db_repo)
    db_session.flush()
    log.info("Resolving dependencies")
    for pkg in packages:
        resolve_dependencies(db_session, sack, db_repo, pkg, group)
    log.info("Computing dependency differences")
    process_dependency_differences(db_session)
    log.info("Computing dependency distances")
    for pkg in packages:
        compute_dependency_distance(db_session, sack, pkg)
    db_session.commit()
    cleanup_deps(db_session, db_repo)
    log.info("New repo done")
