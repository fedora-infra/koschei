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

import rpm

from sqlalchemy import create_engine, Column, Integer, String, Boolean, \
                       ForeignKey, DateTime
from sqlalchemy.sql.expression import extract, func, select, join, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, mapper, column_property
from sqlalchemy.engine.url import URL
from datetime import datetime
# Python 2 only
from itertools import izip_longest

from .util import config

Base = declarative_base()

db_url = URL(**config['database_config'])
engine = create_engine(db_url, echo=False, pool_size=10)

Session = sessionmaker(bind=engine, autocommit=False)

def hours_since(since):
    return extract('EPOCH', datetime.now() - since) / 3600

class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    builds = relationship('Build', backref='package', lazy='dynamic')
    static_priority = Column(Integer, nullable=False, default=0)
    manual_priority = Column(Integer, nullable=False, default=0)
    added = Column(DateTime, nullable=False, default=datetime.now)

    build_opts = Column(String)

    # last_build defined later

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

    @property
    def state_string(self):
        if self.state == self.OK:
            return self.last_build.state_string
        elif self.state == self.UNRESOLVED:
            return 'unresolved'
        elif self.state == self.IGNORED:
            return 'ignored'
        elif self.state == self.RETIRED:
            return 'retired'

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)

class PackageGroupRelation(Base):
    __tablename__ = 'package_group_relation'
    group_id = Column(Integer, ForeignKey('package_group.id'), primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id'), primary_key=True)

class PackageGroup(Base):
    __tablename__ = 'package_group'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)

    packages = relationship(Package, secondary=PackageGroupRelation.__table__,
                            order_by=Package.name)

class Build(Base):
    __tablename__ = 'build'

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id'))
    state = Column(Integer, nullable=False, default=0)
    task_id = Column(Integer)
    logs_downloaded = Column(Boolean, default=False, nullable=False)
    started = Column(DateTime)
    finished = Column(DateTime)
    epoch = Column(Integer)
    version = Column(String)
    release = Column(String)
    dependency_changes = relationship('DependencyChange', backref='applied_in',
                                      order_by='DependencyChange.distance')
    # was the build done by koschei or was it real build done by packager
    real = Column(Boolean, nullable=False, server_default='false')

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
        return [change.get_trigger() for change in self.dependency_changes]

    @property
    def buildroot_diff_per_arch(self):
        return [(diff.arch, diff) for diff in self.buildroot_diff]

    def __repr__(self):
        return '{0.id} (name={0.package.name}, state={0.state_string})'.format(self)

class BuildrootDiff(Base):
    __tablename__ = 'buildroot_diff'
    id = Column(Integer, primary_key=True)
    prev_build_id = Column(ForeignKey(Build.id))
    curr_build_id = Column(ForeignKey(Build.id))
    arch = Column(String)
    added = Column(String)
    removed = Column(String)

    @property
    def zipped_diff(self):
        added = self.added.split(',')
        removed = self.removed.split(',')
        return izip_longest(added, removed)

class Repo(Base):
    __tablename__ = 'repo'
    id = Column(Integer, primary_key=True)
    generated = Column(DateTime, nullable=False, default=datetime.now)

class ResolutionResult(Base):
    __tablename__ = 'resolution_result'
    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id'))
    repo_id = Column(ForeignKey('repo.id'))
    resolved = Column(Boolean, nullable=False)
    problems = relationship('ResolutionProblem')

class ResolutionProblem(Base):
    __tablename__ = 'resolution_result_element'
    id = Column(Integer, primary_key=True)
    resolution_id = Column(Integer, ForeignKey(ResolutionResult.id))
    problem = Column(String, nullable=False)

class Dependency(Base):
    __tablename__ = 'dependency'
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey('repo.id'))
    package_id = Column(ForeignKey('package.id'))
    name = Column(String, nullable=False)
    epoch = Column(Integer)
    version = Column(String, nullable=False)
    release = Column(String, nullable=False)
    arch = Column(String, nullable=False)

    nevra = (name, epoch, version, release, arch)

update_weight = config['priorities']['package_update']

def format_evr(epoch, version, release):
    if not version or not release:
        return ''
    if epoch:
        return '{}:{}-{}'.format(epoch, version, release)
    return '{}-{}'.format(version, release)

class DependencyChange(Base):
    __tablename__ = 'dependency_change'
    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id'), nullable=False)
    applied_in_id = Column(ForeignKey('build.id'), nullable=True, default=None)
    dep_name = Column(String, nullable=False)
    prev_epoch = Column(Integer)
    prev_version = Column(String)
    prev_release = Column(String)
    curr_epoch = Column(Integer)
    curr_version = Column(String)
    curr_release = Column(String)
    distance = Column(Integer)

    @property
    def prev_dep_evr(self):
        return format_evr(self.prev_epoch, self.prev_version, self.prev_release)

    @property
    def curr_dep_evr(self):
        return format_evr(self.curr_epoch, self.curr_version, self.curr_release)

    @property
    def is_update(self):
        prev = (str(self.prev_epoch), self.prev_version, self.prev_release)
        curr = (str(self.curr_epoch), self.curr_version, self.curr_release)
        return rpm.labelCompare(prev, curr) < 0

    @classmethod
    def get_priority_query(cls, db_session):
        return db_session.query(cls.package_id.label('pkg_id'),
                             (update_weight / cls.distance).label('priority'))\
                         .filter_by(applied_in_id=None)\
                         .filter(cls.distance > 0)

    @classmethod
    def build_submitted(cls, db_session, build):
        db_session.query(cls).filter_by(package_id=build.package_id)\
                             .filter_by(applied_in_id=None)\
                             .update({'applied_in_id': build.id})

def max_relationship(cls, group_by, filt=None, alias=None):
    max_expr = select([func.max(cls.id).label('m'), group_by])\
                     .group_by(group_by)
    if filt is not None:
        max_expr = max_expr.where(filt)
    max_expr = max_expr.alias()
    joined = select([cls]).select_from(join(cls, max_expr,
                                            cls.id == max_expr.c.m)).alias(alias)
    return relationship(mapper(cls, joined, non_primary=True), uselist=False)

# Relationships

Package.last_build = max_relationship(Build, Build.package_id,
                                      filt=or_(Build.state == Build.COMPLETE,
                                               Build.state == Build.FAILED),
                                      alias='last_build')
Package.all_builds = relationship(Build, order_by=Build.id.desc())
Package.resolution_result = max_relationship(ResolutionResult, ResolutionResult.package_id)
Build.buildroot_diff = relationship(BuildrootDiff,
            primaryjoin=(BuildrootDiff.curr_build_id == Build.id))

PackageGroup.package_count = column_property(
        select([func.count(PackageGroupRelation.group_id)],
               PackageGroupRelation.group_id == PackageGroup.id)\
               .correlate(PackageGroup).as_scalar(),
        deferred=True)
