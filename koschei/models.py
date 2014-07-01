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

from sqlalchemy import create_engine, Column, Integer, String, Boolean, \
                       ForeignKey, DateTime, literal_column
from sqlalchemy.sql.expression import extract, func
from sqlalchemy.ext.declarative import declarative_base, AbstractConcreteBase, \
                                       declared_attr
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.engine.url import URL
from datetime import datetime

from .util import config

Base = declarative_base()

db_url = URL(**config['database_config'])
engine = create_engine(db_url, echo=False, pool_size=10)

Session = sessionmaker(bind=engine, autocommit=False)

def hours_since(since):
    return extract('EPOCH', datetime.now() - since) / 3600

# TODO trigger?
#class ChangeExtension(SessionExtension):
#    def before_flush(self, session, flush_context, instances):
#        for instance in session.dirty:
#            if not session.is_modified(instance, passive=True):
#                continue
#            if not attributes.instance_state(instance).has_identity:
#                continue
#            if isinstance(instance, Package):
#                change = PackageChange(package_id=instance.id,
#                                       prev_state=instance)
#                instance.new_version(session)
#                session.add(instance)

class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    builds = relationship('Build', backref='package', lazy='dynamic')
    static_priority = Column(Integer, nullable=False, default=0)
    manual_priority = Column(Integer, nullable=False, default=0)
    added = Column(DateTime, nullable=False, default=datetime.now)

    OK = 0
    UNRESOLVED = 1
    IGNORED = 2
    RETIRED = 3
    state = Column(Integer, nullable=False, server_default=str(OK))

    @staticmethod
    def time_since_added():
        return extract('EPOCH', datetime.now() - Package.added) / 3600

    def get_builds_in_interval(self, since=None, until=None):
        filters = [Build.state.in_(Build.FINISHED_STATES + [Build.RUNNING])]
        if since:
            filters.append(Build.started >= since)
        if until:
            filters.append(Build.started < until)
        return self.builds.filter(*filters).order_by(Build.started)

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)

class Build(Base):
    __tablename__ = 'build'

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id'))
    state = Column(Integer, nullable=False, default=0)
    task_id = Column(Integer)
    logs_downloaded = Column(Boolean, default=False, nullable=False)
    started = Column(DateTime)
    finished = Column(DateTime)

    @staticmethod
    def time_since_last_build_expr():
        return extract('EPOCH', datetime.now() - func.max(Build.started)) / 3600

    STATE_MAP = {'scheduled': 0,
                 'running': 2,
                 'complete': 3,
                 'canceled': 4,
                 'failed': 5,
                }
    SCHEDULED = STATE_MAP['scheduled']
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    CANCELED = STATE_MAP['canceled']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    UNFINISHED_STATES = [SCHEDULED, RUNNING]
    FINISHED_STATES = [COMPLETE, FAILED, CANCELED]
    STATES = UNFINISHED_STATES + FINISHED_STATES

    KOJI_STATE_MAP = {'CLOSED': COMPLETE,
                      'CANCELED': CANCELED,
                      'FAILED': FAILED}

    @property
    def state_string(self):
        return self.REV_STATE_MAP[self.state]

    @property
    def triggers(self):
        s = Session.object_session(self)
        triggers = []
        for cls in PackageStateChange, DependencyChange:
            changes = s.query(cls).filter_by(applied_in_id=self.id).all()
            if changes:
                triggers += [change.get_trigger() for change in changes]
                break
        return triggers

    def __repr__(self):
        return '{0.id} (name={0.package.name}, state={0.state_string})'.format(self)

class Change(AbstractConcreteBase, Base):
    __abstract__ = True
    id = Column(Integer, primary_key=True)
    @declared_attr
    def package_id(self):
        return Column(ForeignKey('package.id'), nullable=False)
    @declared_attr
    def applied_in_id(self):
        return Column(ForeignKey('build.id'), nullable=True, default=None)

    @classmethod
    def query(cls, db_session, *what):
        return db_session.query(*what or (cls,)).filter_by(applied_in_id=None)

    @classmethod
    def get_priority_query(cls, db_session):
        raise NotImplementedError()

    @classmethod
    def build_submitted(cls, db_session, build):
        cls.query(db_session).update({'applied_in_id': build.id})

    def get_trigger(self):
        raise NotImplementedError()

class PackageStateChange(Change):
    __tablename__ = 'package_change'
    prev_state = Column(Integer)
    curr_state = Column(Integer)

    @classmethod
    def get_priority_query(cls, db_session):
        # workaroud for literal_column not working, cls.curr_state is 0
        return cls.query(db_session, cls.package_id, cls.curr_state + 30)\
                  .filter(cls.prev_state != Package.OK)\
                  .filter(cls.curr_state == Package.OK)

#    @classmethod
#    def build_submitted(cls, db_session, build):
#        # squash changes between builds
#        unapplied_query = cls.query(db_session)
#        unapplied = unapplied_query.all()
#        if len(unapplied) > 1:
#            new_change = cls(package_id=unapplied[0].package_id,
#                             prev_state=unapplied[0].prev_state,
#                             curr_state=unapplied[-1].curr_state)
#            db_session.add(new_change)
#            unapplied_query.delete()
#            db_session.commit()

    def get_trigger(self):
        if self.curr_state != Package.OK:
            return {
                Package.UNRESOLVED: 'Package dependencies became satisfied',
                Package.IGNORED: 'Package became watched again',
                Package.RETIRED: 'Package unretired',
                None: 'Package added'
            }[self.prev_state]

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

class DependencyChange(Change):
    __tablename__ = 'dependency_change'
    dep_name = Column(String, nullable=False)
    prev_dep_evr = Column(String)
    curr_dep_evr = Column(String)
    weight = Column(Integer)

    @classmethod
    def get_priority_query(cls, db_session):
        return cls.query(db_session, cls.package_id, cls.weight)

    def get_trigger(self):
        if self.prev_dep_evr and self.curr_dep_evr:
            if self.prev_dep_evr < self.curr_dep_evr:
                up_dn = 'updated'
            else:
                up_dn = 'downgraded'
            return 'Dependency {} was {} from {} to {}'\
                   .format(self.dep_name, up_dn, self.prev_dep_evr,
                           self.curr_dep_evr)
        elif self.prev_dep_evr:
            return 'Dependency {} disappeared'.format(self.dep_name)
        else:
            return 'Dependency {} appeared'.format(self.dep_name)
