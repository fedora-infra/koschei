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

import koji

import sqlalchemy
from sqlalchemy import (create_engine, Table, Column, Integer, String, Boolean,
                        ForeignKey, DateTime, Index, DDL, Float)
from sqlalchemy.sql.expression import func, select, false, true
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import (sessionmaker, relationship, column_property,
                            configure_mappers)
from sqlalchemy.engine.url import URL
from sqlalchemy.event import listen
from datetime import datetime

from .util import config, primary_koji_config, secondary_koji_config

Base = declarative_base()

db_url = config.get('database_url') or URL(**config['database_config'])
engine = create_engine(db_url, echo=False, pool_size=10)


def external_id():
    raise AssertionError("ID needs to be supplied")


class Query(sqlalchemy.orm.Query):
    # TODO move get_or_create here
    # def get_or_create(self):
    #     items = self.all()
    #     if items:
    #         if len(items) > 1:
    #             raise ProgrammerError("get_or_create query returned more than one item")
    #         return items[0]
    #     entity = self._primary_entity.entities[0]
    #     how to get args?
    #     self.session.add(entity(**args))

    def lock_rows(self):
        """
        Locks rows satisfying given filter expression in consistent order.
        Should eliminate deadlocks with other transactions that do the same before
        attempting to update.
        """
        mapper = self._only_full_mapper_zero("lock_rows")
        self.order_by(*mapper.primary_key)\
            .with_lockmode('update')\
            .all()

    def all_flat(self):
        return [x for [x] in self.all()]


Session = sessionmaker(bind=engine, autocommit=False, query_cls=Query)


def get_or_create(db, table, **cond):
    """
    Returns a row from table that satisfies cond or a new row if no such row
    exists yet. Can still cause IntegrityError in concurrent environment.
    """
    item = db.query(table).filter_by(**cond).first()
    if not item:
        item = table(**cond)
        db.add(item)
    return item


class User(Base):
    __tablename__ = 'user'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String)
    timezone = Column(String)
    admin = Column(Boolean, nullable=False, server_default=false())

    # Whether packages for this user were retrieved. Setting to false invalidates cache
    packages_retrieved = Column(Boolean, nullable=False, server_default=false())


class Collection(Base):
    __tablename__ = 'collection'

    id = Column(Integer, primary_key=True)
    # name used in machine context (urls, fedmsg), e.g. "f24"
    name = Column(String, nullable=False, unique=True)
    # name for ordinary people, e.g. "Fedora 24"
    display_name = Column(String, nullable=False)

    target_tag = Column(String, nullable=False)
    build_tag = Column(String, nullable=False)

    # priority of packages in given collection is multiplied by this
    priority_coefficient = Column(Float, nullable=False, server_default='1')

    latest_repo_id = Column(Integer)
    latest_repo_resolved = Column(Boolean)

    packages = relationship('Package', backref='collection', passive_deletes=True)

    def is_buildroot_broken(self):
        return self.latest_repo_resolved is False # None means unknown

    def __str__(self):
        return self.display_name


class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    static_priority = Column(Integer, nullable=False, default=0)
    manual_priority = Column(Integer, nullable=False, default=0)
    added = Column(DateTime, nullable=False, default=datetime.now)
    collection_id = Column(Integer, ForeignKey(Collection.id, ondelete='CASCADE'),
                           nullable=False)
    collection = None # backref, shut up pylint

    arch_override = Column(String)

    # cached value, populated by scheduler
    current_priority = Column(Integer)

    # denormalized fields, updated by trigger on inser/update (no delete)
    last_complete_build_id = \
        Column(Integer, ForeignKey('build.id', use_alter=True,
                                   name='fkey_package_last_complete_build_id'),
               nullable=True)
    last_complete_build_state = Column(Integer)
    last_build_id = \
        Column(Integer, ForeignKey('build.id', use_alter=True,
                                   name='fkey_package_last_build_id',
                                   # it's first updated by trigger, this is
                                   # fallback, when there's nothing to update
                                   # it to
                                   ondelete='SET NULL'),
               nullable=True)
    resolved = Column(Boolean)

    resolution_problems = relationship('ResolutionProblem', backref='package',
                                       passive_deletes=True)

    tracked = Column(Boolean, nullable=False, server_default=true())
    blocked = Column(Boolean, nullable=False, server_default=false())

    SKIPPED_NO_SRPM = 1
    scheduler_skip_reason = Column(Integer)

    def get_state(self):
        if self.blocked:
            return 'blocked'
        if not self.tracked:
            return 'untracked'
        if self.resolved is False:
            return 'unresolved'
        if self.last_complete_build_state is not None:
            return {Build.COMPLETE: 'ok',
                    Build.FAILED: 'failing'}.get(self.last_complete_build_state)

    @property
    def state_string(self):
        """String representation of state used when disaplying to user"""
        return self.get_state() or 'unknown'

    @property
    def msg_state_string(self):
        """String representation of state used when publishing messages"""
        # TODO distinguish between blocked and untracked without breaking fedmsg format
        return not self.blocked and self.tracked and self.get_state() or 'ignored'

    @property
    def has_running_build(self):
        return self.last_build_id != self.last_complete_build_id

    @property
    def srpm_nvra(self):
        return dict(name=self.name,
                    version=self.last_complete_build.version,
                    release=self.last_complete_build.release,
                    arch='src') if self.last_complete_build else None

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)


class UserPackageRelation(Base):
    __tablename__ = 'user_package_relation'
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'),
                     primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'),
                        primary_key=True)


class KojiTask(Base):
    __tablename__ = 'koji_task'

    id = Column(Integer, primary_key=True)
    build_id = Column(ForeignKey('build.id', ondelete='CASCADE'),
                      nullable=False, index=True)
    task_id = Column(Integer, nullable=False)
    arch = Column(String(16))
    state = Column(Integer)
    started = Column(DateTime)
    finished = Column(DateTime)

    @property
    def state_string(self):
        return [state for state, num in koji.TASK_STATES.items()
                if num == self.state][0].lower()

    @property
    def _koji_config(self):
        # pylint:disable=no-member
        return secondary_koji_config if self.build.real else primary_koji_config

    @property
    def results_url(self):
        pathinfo = koji.PathInfo(topdir=self._koji_config['topurl'])
        return pathinfo.task(self.task_id)

    @property
    def taskinfo_url(self):
        return '{}/taskinfo?taskID={}'.format(self._koji_config['weburl'], self.task_id)


class PackageGroupRelation(Base):
    __tablename__ = 'package_group_relation'
    group_id = Column(Integer, ForeignKey('package_group.id',
                                          ondelete='CASCADE'),
                      primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'),
                        primary_key=True)


class GroupACL(Base):
    __tablename__ = 'group_acl'

    group_id = Column(Integer, ForeignKey('package_group.id',
                                          ondelete='CASCADE'),
                      primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id',
                                         ondelete='CASCADE'),
                     primary_key=True)


class PackageGroup(Base):
    __tablename__ = 'package_group'

    id = Column(Integer, primary_key=True)
    namespace = Column(String)
    name = Column(String, nullable=False)

    packages = relationship(Package, secondary=PackageGroupRelation.__table__,
                            order_by=Package.name, passive_deletes=True)
    owners = relationship(User, secondary=GroupACL.__table__,
                          order_by=User.name, passive_deletes=True)

    @property
    def full_name(self):
        if self.namespace:
            return self.namespace + '/' + self.name
        return self.name

    @property
    def owners_list(self):
        return ', '.join(u.name for u in self.owners)


class Build(Base):
    __tablename__ = 'build'

    STATE_MAP = {'running': 2,
                 'complete': 3,
                 'canceled': 4,
                 'failed': 5}
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    CANCELED = STATE_MAP['canceled']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    FINISHED_STATES = [COMPLETE, FAILED, CANCELED]
    STATES = [RUNNING] + FINISHED_STATES

    KOJI_STATE_MAP = {'CLOSED': COMPLETE,
                      'CANCELED': CANCELED,
                      'FAILED': FAILED}

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'))
    package = None # backref
    state = Column(Integer, nullable=False, default=RUNNING)
    task_id = Column(Integer)
    started = Column(DateTime)
    finished = Column(DateTime)
    epoch = Column(Integer)
    version = Column(String)
    release = Column(String)
    repo_id = Column(Integer)

    # deps_processed means we tried to resolve them, deps_resolved means we
    # succeeded
    deps_processed = Column(Boolean, nullable=False, server_default=false())
    deps_resolved = Column(Boolean, nullable=False, server_default=false())

    build_arch_tasks = relationship(KojiTask, backref='build',
                                    order_by=KojiTask.arch,
                                    passive_deletes=True)
    # was the build done by koschei or was it real build done by packager
    real = Column(Boolean, nullable=False, server_default=false())

    dependencies = relationship('Dependency', backref='build',
                                passive_deletes=True)

    @property
    def state_string(self):
        return self.REV_STATE_MAP[self.state]

    @property
    def srpm_nvra(self):
        # pylint:disable=no-member
        return dict(name=self.package.name,
                    version=self.version,
                    release=self.release,
                    arch='src')

    @property
    def taskinfo_url(self):
        koji_config = secondary_koji_config if self.real else primary_koji_config
        return '{}/taskinfo?taskID={}'.format(koji_config['weburl'], self.task_id)

    def __repr__(self):
        # pylint: disable=W1306
        return ('{b.id} (name={b.package.name}, state={b.state_string}, '
                'task_id={b.task_id})').format(b=self)


class ResolutionProblem(Base):
    __tablename__ = 'resolution_problem'
    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey(Package.id,
                                            ondelete='CASCADE'),
                        nullable=False, index=True)
    problem = Column(String, nullable=False)


class Dependency(Base):
    __tablename__ = 'dependency'
    id = Column(Integer, primary_key=True)
    build_id = Column(ForeignKey('build.id', ondelete='CASCADE'), index=True,
                      nullable=False)
    name = Column(String, nullable=False)
    epoch = Column(Integer)
    version = Column(String, nullable=False)
    release = Column(String, nullable=False)
    arch = Column(String, nullable=False)
    distance = Column(Integer)

    nevr = (name, epoch, version, release)
    nevra = (name, epoch, version, release, arch)


Index('ix_build_running', Build.package_id, unique=True,
      postgresql_where=(Build.state == Build.RUNNING))
Index('ix_build_composite', Build.package_id, Build.id.desc())
Index('ix_package_group_name', PackageGroup.namespace, PackageGroup.name,
      unique=True)


class DependencyChange(object):
    # not an actual table
    id = Column(Integer, primary_key=True)
    dep_name = Column(String, nullable=False)
    prev_epoch = Column(Integer)
    prev_version = Column(String)
    prev_release = Column(String)
    curr_epoch = Column(Integer)
    curr_version = Column(String)
    curr_release = Column(String)
    distance = Column(Integer)

    @property
    def prev_evr(self):
        return self.prev_epoch, self.prev_version, self.prev_release

    @property
    def curr_evr(self):
        return self.curr_epoch, self.curr_version, self.curr_release


class AppliedChange(DependencyChange, Base):
    __tablename__ = 'applied_change'
    build_id = Column(ForeignKey('build.id', ondelete='CASCADE'), index=True,
                      nullable=False)
    # needs to be nullable because we delete old builds
    prev_build_id = Column(ForeignKey('build.id', ondelete='SET NULL'),
                           index=True)


class UnappliedChange(DependencyChange, Base):
    __tablename__ = 'unapplied_change'
    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'),
                        index=True, nullable=False)
    prev_build_id = Column(ForeignKey('build.id', ondelete='CASCADE'),
                           index=True, nullable=False)


class BuildrootProblem(Base):
    __tablename__ = 'buildroot_problem'
    id = Column(Integer, primary_key=True)
    collection_id = Column(ForeignKey(Collection.id, ondelete='CASCADE'), index=True)
    problem = Column(String, nullable=False)


class AdminNotice(Base):
    __tablename__ = 'admin_notice'
    key = Column(String, primary_key=True)
    content = Column(String, nullable=False)

# Triggers

trigger = DDL("""
              CREATE OR REPLACE FUNCTION update_last_complete_build()
                  RETURNS TRIGGER AS $$
              BEGIN
                  UPDATE package
                  SET last_complete_build_id = lcb.id,
                      last_complete_build_state = lcb.state
                  FROM (SELECT id, state
                        FROM build
                        WHERE package_id = NEW.package_id
                              AND (state = 3 OR state = 5)
                        ORDER BY id DESC
                        LIMIT 1) AS lcb
                  WHERE package.id = NEW.package_id;
                  RETURN NEW;
              END $$ LANGUAGE plpgsql;
              CREATE OR REPLACE FUNCTION update_last_build()
                  RETURNS TRIGGER AS $$
              BEGIN
                  UPDATE package
                  SET last_build_id = lb.id
                  FROM (SELECT id, state, started
                        FROM build
                        WHERE package_id = NEW.package_id
                        ORDER BY id DESC
                        LIMIT 1) AS lb
                  WHERE package.id = NEW.package_id;
                  RETURN NEW;
              END $$ LANGUAGE plpgsql;
              CREATE OR REPLACE FUNCTION update_last_build_del()
                  RETURNS TRIGGER AS $$
              BEGIN
                  UPDATE package
                  SET last_build_id = lb.id
                  FROM (SELECT id, state, started
                        FROM build
                        WHERE package_id = OLD.package_id
                              AND build.id != OLD.id
                        ORDER BY id DESC
                        LIMIT 1) AS lb
                  WHERE package.id = OLD.package_id;
                  RETURN OLD;
              END $$ LANGUAGE plpgsql;

              DROP TRIGGER IF EXISTS update_last_complete_build_trigger
                    ON build;
              DROP TRIGGER IF EXISTS update_last_build_trigger
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  WHEN (NEW.state = 3 OR NEW.state = 5)
                  EXECUTE PROCEDURE update_last_complete_build();
              CREATE TRIGGER update_last_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  EXECUTE PROCEDURE update_last_build();
              DROP TRIGGER IF EXISTS update_last_complete_build_trigger_up
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger_up
                  AFTER UPDATE ON build FOR EACH ROW
                  WHEN (OLD.state != NEW.state)
                  EXECUTE PROCEDURE update_last_complete_build();
              DROP TRIGGER IF EXISTS update_last_build_trigger_del
                    ON build;
              CREATE TRIGGER update_last_build_trigger_del
                  BEFORE DELETE ON build FOR EACH ROW
                  EXECUTE PROCEDURE update_last_build_del();
              """)

listen(Base.metadata, 'after_create', trigger.execute_if(dialect='postgresql'))


def grant_db_access(_, conn, *args, **kwargs):
    user = config.get('unpriv_db_username')
    if user:
        conn.execute("""
                     GRANT SELECT, INSERT, UPDATE, DELETE
                     ON ALL TABLES IN SCHEMA PUBLIC TO {user};
                     GRANT SELECT, USAGE ON ALL SEQUENCES
                     IN SCHEMA PUBLIC TO {user};
                     """.format(user=user))


listen(Table, 'after_create', grant_db_access)

# Relationships

Package.last_complete_build = \
    relationship(Build,
                 primaryjoin=(Build.id == Package.last_complete_build_id),
                 uselist=False)
Package.last_build = \
    relationship(Build,
                 primaryjoin=(Build.id == Package.last_build_id),
                 uselist=False)

Package.all_builds = relationship(Build, order_by=Build.id.desc(),
                                  primaryjoin=(Build.package_id == Package.id),
                                  backref='package', passive_deletes=True)
Package.unapplied_changes = \
    relationship(UnappliedChange, backref='package',
                 order_by=[UnappliedChange.distance, UnappliedChange.dep_name])
Build.dependency_changes = relationship(AppliedChange, backref='build',
                                        primaryjoin=(Build.id == AppliedChange.build_id),
                                        order_by=AppliedChange.distance
                                        .nullslast(), passive_deletes=True)

PackageGroup.package_count = column_property(
    select([func.count(PackageGroupRelation.group_id)],
           PackageGroupRelation.group_id == PackageGroup.id)
    .correlate(PackageGroup).as_scalar(),
    deferred=True)
# pylint: disable=E1101
Package.groups = relationship(PackageGroup,
                              secondary=PackageGroupRelation.__table__,
                              order_by=PackageGroup.name, passive_deletes=True)
User.packages = relationship(Package,
                             secondary=UserPackageRelation.__table__,
                             passive_deletes=True)
User.groups = relationship(PackageGroup,
                           secondary=GroupACL.__table__,
                           order_by=[PackageGroup.namespace,
                                     PackageGroup.name], passive_deletes=True)

configure_mappers()
