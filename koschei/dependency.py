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

from sqlalchemy import except_, or_
from sqlalchemy.sql.expression import func

from koschei.models import Package, Dependency, DependencyChange, Repo, \
                           PackageStateChange
from koschei import util

def get_srpm_pkg(sack, name):
    hawk_pkg = hawkey.Query(sack).filter(name=name, arch='src',
                                         latest_per_arch=True)[0]
    return hawk_pkg

def resolve_dependencies(db_session, sack, repo, package):
    hawk_pkg = get_srpm_pkg(sack, package.name)
    goal = hawkey.Goal(sack)
    goal.install(hawk_pkg)
    goal.run()
    try:
        installs = goal.list_installs()
    except hawkey.RuntimeException:
        package.state = Package.UNRESOLVED
        return False
    for install in installs:
        if install.arch != 'src':
            dep = Dependency(repo_id=repo.id, package_id=package.id,
                             name=install.name, evr=install.evr, arch=install.arch)
            db_session.add(dep)
            db_session.flush()
    return True

def get_dependency_differences(db_session):
    def get_deps_from_repo(repo):
        return db_session.query(Dependency.package_id, Dependency.name,
                                Dependency.evr)\
                         .filter(Dependency.repo_id == repo)
    def difference_query(q1, q2):
        resolved = [db_session.query(Dependency.package_id)\
                              .select_entity_from(q.subquery().select())
                    for q in (q2, q1)]
        filtered = [q.filter(Dependency.package_id.in_(r)).subquery().select()
                    for q, r in zip((q1, q2), resolved)]
        return db_session.query(Dependency.package_id, Dependency.name,
                                Dependency.evr)\
                         .select_entity_from(except_(*filtered))
    curr_repo = db_session.query(func.max(Repo.id)).subquery()
    prev_repo = db_session.query(func.max(Repo.id) - 1).subquery()
    curr = get_deps_from_repo(curr_repo)
    prev = get_deps_from_repo(prev_repo)
    add_diff = difference_query(curr, prev)
    rm_diff = difference_query(prev, curr)
    return add_diff, rm_diff

def process_dependency_differences(db_session):
    add_diff, rm_diff = get_dependency_differences(db_session)
    changes = {}
    for pkg_id, dep_name, dep_evr in add_diff:
        change = DependencyChange(package_id=pkg_id, dep_name=dep_name,
                                  curr_dep_evr=dep_evr)
        changes[(pkg_id, dep_name)] = change
    for pkg_id, dep_name, dep_evr in rm_diff:
        update = changes.get((pkg_id, dep_name))
        if update:
            update.prev_dep_evr = dep_evr
        else:
            change = DependencyChange(package_id=pkg_id, dep_name=dep_name,
                                      curr_dep_evr=dep_evr)
            changes[(pkg_id, dep_name)] = change
    for change in changes.values():
        db_session.add(change)
    db_session.flush()

def compute_dependency_weight(db_session, sack, package):
    changes = DependencyChange.query(db_session)\
                        .filter(DependencyChange.package_id == package.id,
                                DependencyChange.curr_dep_evr != None).all()
    if not changes:
        return
    changes_map = {change.dep_name: change for change in changes}
    hawk_pkg = get_srpm_pkg(sack, package.name)
    visited = set()
    level = 1
    reldeps = hawk_pkg.requires
    while level < 4 and reldeps:
        pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))
        reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                           for req in pkg.requires}
        visited.update(pkgs_on_level)
        for pkg in pkgs_on_level:
            if pkg.name in changes_map and not changes_map[pkg.name].weight:
                changes_map[pkg.name].weight = 30 // level
        level += 1
    db_session.flush()

def repo_done(self, db_session):
    packages = db_session.query(Package)\
                         .filter(or_(Package.state == Package.OK,
                                     Package.state == Package.UNRESOLVED))
    package_names = [pkg.name for pkg in packages]
    sack = util.create_sack(package_names)
    db_repo = Repo()
    db_session.add(db_repo)
    db_session.flush()
    for pkg in packages:
        resolve_dependencies(db_session, sack, db_repo, pkg)
    process_dependency_differences(db_session)
    for pkg in packages:
        compute_dependency_weight(db_session, sack, pkg)
    db_session.commit()
