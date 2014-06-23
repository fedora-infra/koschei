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

from datetime import datetime

from sqlalchemy import Column, Integer, ForeignKey, String, DateTime, except_
from sqlalchemy.sql.expression import func

from koschei.models import BuildTrigger, Base, Package
from koschei.plugin import Plugin
from koschei import util

class Repo(Base):
    __tablename__ = 'repo'
    id = Column(Integer, primary_key=True)
    generated = Column(DateTime, nullable=False, default=datetime.now)

class Dependency(Base):
    __tablename__ = 'dependency'
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey('repo.id'))
    package_id = Column(ForeignKey('package.id'))
    name = Column(String, nullable=False)
    evr = Column(String, nullable=False)
    arch = Column(String, nullable=False)

class DependencyChange(Base):
    __tablename__ = 'dependency_change'
    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id'))
    dep_name = Column(String, nullable=False)
    prev_dep_evr = Column(String)
    curr_dep_evr = Column(String)
    weight = Column(Integer)

def resolve_dependencies(db_session, sack, repo, package):
    hawk_pkg = hawkey.Query(sack).filter(name=package.name, arch='src')[0]
    goal = hawkey.Goal(sack)
    goal.install(hawk_pkg)
    goal.run()
    try:
        installs = goal.list_installs()
    except hawkey.RuntimeException:
        #TODO set as unbuildable
        return False
    for install in installs:
        dep = Dependency(repo_id=repo.id, package_id=package.id,
                         name=install.name, evr=install.evr, arch=install.arch)
        db_session.add(dep)
        db_session.commit()
    return True

def get_dependency_differences(db_session):
    def get_deps_from_repo(repo):
        return db_session.query(Dependency.package_id, Dependency.name,
                                Dependency.evr)\
                         .filter(Dependency.repo_id == repo)
    def difference_query(q1, q2):
        return db_session.query(Dependency.package_id, Dependency.name,
                                Dependency.evr)\
                         .select_entity_from(except_(q1.select(), q2.select()))
    curr_repo = db_session.query(func.max(Repo.id)).subquery()
    prev_repo = db_session.query(func.max(Repo.id) - 1).subquery()
    curr = get_deps_from_repo(curr_repo).subquery()
    prev = get_deps_from_repo(prev_repo).subquery()
    add_diff = difference_query(prev, curr)
    rm_diff = difference_query(curr, prev)
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
        db_session.commit()

def compute_dependency_weight(db_session, sack, package):
    changes = db_session.query(DependencyChange)\
                        .filter(DependencyChange.package_id == package.id,
                                DependencyChange.curr_dep_evr != None).all()
    if not changes:
        return
    changes_map = {change.dep_name: change for change in changes}
    hawk_pkg = hawkey.Query(sack).filter(name=package.name, arch='src')[0]
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
                db_session.commit()
        level += 1

class DependencyPlugin(Plugin):
    def __init__(self):
        super(DependencyPlugin, self).__init__()
        self.register_event('repo_done', self.repo_done)
        self.register_event('get_priority_query', self.get_priority_query)
        self.register_event('build_submitted', self.populate_triggers)

    def repo_done(self, db_session):
        packages = db_session.query(Package).filter_by(watched=True)
        package_names = [pkg.name for pkg in packages]
        sack = util.create_sack(package_names)
        db_repo = Repo()
        db_session.add(db_repo)
        db_session.commit()
        for pkg in packages:
            resolve_dependencies(db_session, sack, db_repo, pkg)
        process_dependency_differences(db_session)
        for pkg in packages:
            compute_dependency_weight(db_session, sack, pkg)

    def get_priority_query(self, db_session):
        q = db_session.query(DependencyChange.package_id, DependencyChange.weight)
        return q.subquery()

    def populate_triggers(self, db_session, build):
        changes = db_session.query(DependencyChange)\
                            .filter_by(package_id=build.package_id)
        for change in changes:
            comment = 'Dependency {} changed'.format(change.dep_name)
            trigger = BuildTrigger(build_id=build.id, comment=comment)
            db_session.add(trigger)
            db_session.commit()
