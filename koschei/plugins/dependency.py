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

from sqlalchemy import Column, Integer, ForeignKey, Boolean, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import func

from koschei.models import Dependency, BuildTrigger, Base, Package
from koschei.plugins import Plugin
from koschei import util

class DependencyUpdate(Base):
    __tablename__ = 'dependency_update'

    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id'), nullable=False)
    dependency_id = Column(ForeignKey('package.id'), nullable=False)
    package = relationship('Package', primaryjoin=(package_id == Package.id))
    dependency = relationship('Package', primaryjoin=(dependency_id == Package.id))
    effective = Column(Boolean, nullable=False, default=False)
    weight = Column(Integer, nullable=False)
    version = Column(String)
    release = Column(String)

class DependencyPlugin(Plugin):
    def __init__(self):
        super(DependencyPlugin, self).__init__()
        self.register_event('repo_done', self.apply_updates)
        self.register_event('build_tagged', self.package_updated)
        self.register_event('get_priority_query', self.get_priority_query)
        self.register_event('build_submitted', self.populate_triggers)

    def get_priority_query(self, db_session):
        q = db_session.query(DependencyUpdate.package_id,
                             func.sum(DependencyUpdate.weight))\
                      .filter(DependencyUpdate.effective == True)\
                      .group_by(DependencyUpdate.package_id)
        return q.subquery()

    def populate_triggers(self, db_session, build):
        updates = db_session.query(DependencyUpdate)\
                            .filter_by(effective=True,
                                       package_id=build.package.id)
        for update in updates:
            comment = 'Dependency {} updated to version {}-{}'\
                      .format(update.dependency.name, update.version,
                              update.release)
            trigger = BuildTrigger(build_id=build.id, comment=comment)
            db_session.add(trigger)
            db_session.commit()
        updates.delete()
        db_session.commit()

    def apply_updates(self, db_session):
        db_session.query(DependencyUpdate).update({'effective': True})
        db_session.commit()

    def package_updated(self, db_session, package, version, release):
        visited = set()
        def recursive_update(pkgs, level=1):
            new_priority = 30 // level # TODO Bulgarian constant
            if new_priority:
                pkg_ids = [pkg.id for pkg in pkgs]
                visited.update(pkg_ids)
                deps = db_session.query(Dependency)\
                       .filter(Dependency.dependency_id.in_(pkg_ids),
                               Dependency.runtime == (level != 1))
                pkgs_on_level = [dep.package for dep in deps if dep.package_id
                                 not in visited]
                if pkgs_on_level:
                    for pkg in pkgs_on_level:
                        if pkg.watched:
                            update = DependencyUpdate(package_id=pkg.id,
                                                      weight=new_priority,
                                                      dependency_id=package.id,
                                                      version=version,
                                                      release=release)
                            db_session.add(update)
                            db_session.commit()
                    recursive_update(pkgs_on_level, level + 1)

        recursive_update({package})
