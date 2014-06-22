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

from sqlalchemy import Column, Integer, ForeignKey, Boolean, String, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import func

from koschei.models import BuildTrigger, Base, Package
from koschei.plugin import Plugin
from koschei import util

class Repo(Base):
    __tablename__ = 'repo'
    id = Column(Integer, primary_key=True)
    generated = Column(DateTime, nullable=False, default=datetime.now)

class DependencyVector(Base):
    __tablename__ = 'dependency_vector'
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey('repo.id'))
    package_id = Column(ForeignKey('package.id'))
    #dependency_id = Column(ForeignKey('rpm.id'))
    dependency = Column(String, nullable=False)
    distance = Column(Integer)

class DependencyPlugin(Plugin):
    def __init__(self):
        super(DependencyPlugin, self).__init__()
        self.register_event('repo_done', self.repo_done)
#        self.register_event('get_priority_query', self.get_priority_query)
#        self.register_event('build_submitted', self.populate_triggers)

    def repo_done(self, db_session):
        packages = db_session.query(Package).filter_by(watched=True)
        package_names = [pkg.name for pkg in packages]
        sack = util.create_sack(package_names)
        db_repo = Repo()
        db_session.add(db_repo)
        db_session.commit()
        for pkg in packages:
            hawk_pkg = hawkey.Query(sack).filter(name=pkg.name, arch='src')[0]
            goal = hawkey.Goal(sack)
            goal.install(hawk_pkg)
            goal.run()
            try:
                installs = goal.list_installs()
            except hawkey.RuntimeException:
                continue
            else:
                for install in installs:
                    dep = DependencyVector(repo_id=db_repo.id, package_id=pkg.id,
                                           dependency=str(install))
                    db_session.add(dep)
                    db_session.commit()

#    def get_priority_query(self, db_session):
#        q = db_session.query(DependencyUpdate.package_id,
#                             func.sum(DependencyUpdate.weight))\
#                      .filter(DependencyUpdate.effective == True)\
#                      .group_by(DependencyUpdate.package_id)
#        return q.subquery()
#
#    def populate_triggers(self, db_session, build):
#        updates = db_session.query(DependencyUpdate)\
#                            .filter_by(effective=True,
#                                       package_id=build.package.id)
#        for update in updates:
#            comment = 'Dependency {} updated to version {}-{}'\
#                      .format(update.dependency.name, update.version,
#                              update.release)
#            trigger = BuildTrigger(build_id=build.id, comment=comment)
#            db_session.add(trigger)
#            db_session.commit()
#        updates.delete()
#        db_session.commit()
#
#    def apply_updates(self, db_session):
#        db_session.query(DependencyUpdate).update({'effective': True})
#        db_session.commit()
#
#    def package_updated(self, db_session, package, version, release):
#        visited = set()
#        def recursive_update(pkgs, level=1):
#            new_priority = 30 // level # TODO Bulgarian constant
#            if new_priority:
#                pkg_ids = [pkg.id for pkg in pkgs]
#                visited.update(pkg_ids)
#                deps = db_session.query(Dependency)\
#                       .filter(Dependency.dependency_id.in_(pkg_ids),
#                               Dependency.runtime == (level != 1))
#                pkgs_on_level = [dep.package for dep in deps if dep.package_id
#                                 not in visited]
#                if pkgs_on_level:
#                    for pkg in pkgs_on_level:
#                        if pkg.watched:
#                            update = DependencyUpdate(package_id=pkg.id,
#                                                      weight=new_priority,
#                                                      dependency_id=package.id,
#                                                      version=version,
#                                                      release=release)
#                            db_session.add(update)
#                            db_session.commit()
#                    recursive_update(pkgs_on_level, level + 1)
#
#        recursive_update({package})
