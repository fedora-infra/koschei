# Copyright (C) 2014-2016  Red Hat, Inc.
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

# pylint:disable=no-self-argument

import math

from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey, DateTime, Index, Float,
    CheckConstraint, UniqueConstraint, Enum, Interval,
)
from sqlalchemy.sql.expression import (
    func, select, join, false, true, extract, case, null, cast,
)
from sqlalchemy.orm import (relationship, column_property,
                            configure_mappers, deferred, composite)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import ARRAY

from .config import get_config
from koschei.db import (
    Base, MaterializedView, CompressedKeyArray, RpmEVR, RpmEVRComparator,
    sql_property,
)


class User(Base):
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    admin = Column(Boolean, nullable=False, server_default=false())


class Collection(Base):
    __table_args__ = (
        CheckConstraint('(latest_repo_resolved IS NULL) = (latest_repo_id IS NULL)',
                        name='collection_latest_repo_id_check'),
    )

    id = Column(Integer, primary_key=True)
    order = Column(Integer, nullable=False, server_default="100")
    # name used in machine context (urls, fedmsg), e.g. "f24"
    name = Column(String, nullable=False, unique=True)
    # name for ordinary people, e.g. "Fedora 24"
    display_name = Column(String, nullable=False)

    # whether this collection is in secondary or primary mode
    secondary_mode = Column(Boolean, nullable=False, server_default=false())

    # Koji configuration
    target = Column(String, nullable=False)
    dest_tag = Column(String, nullable=False)
    build_tag = Column(String, nullable=False)

    # bugzilla template fields. If null, bug filling will be disabled
    bugzilla_product = Column(String)
    bugzilla_version = Column(String)

    # priority of packages in given collection is multiplied by this
    priority_coefficient = Column(Float, nullable=False, server_default='1')

    # build group name
    build_group = Column(String, nullable=False, server_default='build')

    latest_repo_id = Column(Integer)
    latest_repo_resolved = Column(Boolean)

    # whether to poll builds for untracked packages
    poll_untracked = Column(Boolean, nullable=False, server_default=true())

    packages = relationship('Package', backref='collection', passive_deletes=True)

    @property
    def state_string(self):
        return {True: 'ok', False: 'unresolved', None: 'unknown'}[
            self.latest_repo_resolved]

    def __str__(self):
        return self.display_name


class CollectionGroup(Base):
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=False)

    def __str__(self):
        return self.display_name


class CollectionGroupRelation(Base):
    group_id = Column(
        Integer,
        ForeignKey('collection_group.id', ondelete='CASCADE'),
        primary_key=True
    )
    collection_id = Column(
        Integer,
        ForeignKey('collection.id', ondelete='CASCADE'),
        primary_key=True,
    )


class BasePackage(Base):
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    packages = relationship('Package', backref='base', passive_deletes=True)

    # updated by trigger
    all_blocked = Column(Boolean, nullable=False, server_default=true())


class TimePriority(object):
    """
    Container for lazy computation of static time priority inputs
    """
    def __getattr__(self, name):
        assert name == 'inputs'
        t0 = get_config('priorities.t0')
        t1 = get_config('priorities.t1')
        a = get_config('priorities.build_threshold') / (math.log10(t1) - math.log10(t0))
        b = -a * math.log10(t0)
        setattr(self, 'inputs', (a, b))
        return self.inputs


TIME_PRIORITY = TimePriority()


class Package(Base):
    __table_args__ = (
        UniqueConstraint('base_id', 'collection_id',
                         name='package_unique_in_collection'),
        CheckConstraint('NOT skip_resolution OR resolved IS NULL',
                        name='package_skip_resolution_check'),
    )

    id = Column(Integer, primary_key=True)
    base_id = Column(Integer, ForeignKey(BasePackage.id, ondelete='CASCADE'),
                     nullable=False)

    name = Column(String, nullable=False, index=True)  # denormalized from base_package
    collection_id = Column(Integer, ForeignKey(Collection.id, ondelete='CASCADE'),
                           nullable=False)
    collection = None  # backref, shut up pylint

    arch_override = Column(String)
    # causes resolution to be skipped, package can be built even if it would be
    # unresolved otherwise
    skip_resolution = Column(Boolean, nullable=False, server_default=false())

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

    # priority calculation input values
    # priority set by (super)user, never reset by koschei
    static_priority = Column(Integer, nullable=False, server_default='0')
    # priority set by user, reset after a build is registered
    manual_priority = Column(Integer, nullable=False, server_default='0')
    # priority based on build - build started to fail, build failed to resolve,
    # last build wasn't resolved and thus new build is necessary
    build_priority = Column(Integer, nullable=False, server_default='0')
    # priority based on dependency changes since last build
    dependency_priority = Column(Integer, nullable=False, server_default='0')

    tracked = Column(Boolean, nullable=False, server_default=true())
    blocked = Column(Boolean, nullable=False, server_default=false())

    SKIPPED_NO_SRPM = 1
    SKIPPED_NO_ARCH = 2
    scheduler_skip_reason = Column(Integer)

    @classmethod
    def current_priority_expression(cls, collection, last_build):
        """
        Return computed value for packages priority or None if package is not
        schedulable.
        """
        # if last_build is concrete object, it may be None
        # packages with no last build should have no priority
        # (they cannot be scheduled)
        if not last_build:
            return null()

        dynamic_priority = cls.dependency_priority + cls.build_priority

        # compute time priority
        seconds = extract('EPOCH', func.clock_timestamp() - last_build.started)
        a, b = TIME_PRIORITY.inputs
        # avoid zero/negative values, when time difference too small
        log_arg = func.greatest(0.000001, seconds / 3600)
        dynamic_priority += func.greatest(a * func.log(log_arg) + b, -30)

        # dynamic priority is affected by coefficient
        dynamic_priority *= collection.priority_coefficient

        # manual and static priority are not affected by coefficient
        current_priority = cls.manual_priority + cls.static_priority + dynamic_priority

        return case(
            [
                # handle unschedulable packages
                (
                    # WHEN blocked OR untracked
                    cls.blocked | ~cls.tracked |
                    # OR has running build
                    (cls.last_complete_build_id != cls.last_build_id) |
                    # OR is unresolved
                    (cls.resolved == False) |
                    # OR resolution is not yet done
                    ((cls.resolved == None) & ~cls.skip_resolution) |
                    # OR the collection's buildroot is broken
                    (collection.latest_repo_resolved == False) |
                    # OR the collection's buildroot wasn't resolved yet
                    (collection.latest_repo_resolved == None),
                    # THEN return NULL
                    None,
                )
            ],
            # ELSE return the computed priority
            else_=current_priority
        )

    @property
    def skip_reasons(self):
        reasons = []
        if self.scheduler_skip_reason == Package.SKIPPED_NO_SRPM:
            reasons.append("No suitable SRPM was found")
        if self.scheduler_skip_reason == Package.SKIPPED_NO_ARCH:
            reasons.append("No build architecture allowed for SRPM")
        if not self.tracked:
            reasons.append("Package is not tracked")
        if self.blocked:
            reasons.append("Package is blocked in koji")
        if self.last_complete_build_id != self.last_build_id:
            reasons.append("Package has a running build")
        if self.resolved is False:
            reasons.append("Package dependencies are not resolvable")
        if self.resolved is None and not self.skip_resolution:
            reasons.append("Package dependencies were not resolved yet")
        if not self.last_build_id:
            reasons.append("Package has no known build")
        if self.collection.latest_repo_resolved is False:
            reasons.append("Base buildroot for {} is not resolvable"
                           .format(self.collection))
        if self.collection.latest_repo_resolved is None:
            reasons.append("Base buildroot for {} was not resolved yet"
                           .format(self.collection))
        return reasons

    @sql_property
    def state_string(cls):
        """String representation of state used when disaplying to user"""
        return case(
            [
                (cls.blocked, 'blocked'),
                (~cls.tracked, 'untracked'),
                (cls.resolved == False, 'unresolved'),
                (cls.last_complete_build_state == Build.COMPLETE, 'ok'),
                (cls.last_complete_build_state == Build.FAILED, 'failing'),
            ],
            else_='unknown',
        )

    @property
    def msg_state_string(self):
        """String representation of state used when publishing messages"""
        state = self.state_string
        return state if state in ('ok', 'failing', 'unresolved') else 'ignored'

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


class KojiTask(Base):
    __table_args__ = (CheckConstraint('state BETWEEN 0 AND 5',
                                      name='koji_task_state_check'),)

    id = Column(Integer, primary_key=True)
    build_id = Column(ForeignKey('build.id', ondelete='CASCADE'),
                      nullable=False, index=True)
    task_id = Column(Integer, nullable=False)
    arch = Column(String, nullable=False)
    state = Column(Integer, nullable=False)
    started = Column(DateTime, nullable=False)
    finished = Column(DateTime)

    @property
    def state_string(self):
        # return [state for state, num in koji.TASK_STATES.items()
        #         if num == self.state][0].lower()
        # pylint:disable=invalid-sequence-index
        states = ['free', 'open', 'closed', 'canceled', 'assigned', 'failed']
        return states[self.state]

    @property
    def _koji_config(self):
        # pylint:disable=no-member
        if self.build.real:
            return get_config('secondary_koji_config')
        return get_config('koji_config')

    @property
    def results_url(self):
        # pathinfo = koji.PathInfo(topdir=self._koji_config['topurl'])
        # return pathinfo.task(self.task_id)
        return '{}/work/tasks/{}/{}'.format(self._koji_config['topurl'],
                                            self.task_id % 10000, self.task_id)

    @property
    def taskinfo_url(self):
        return '{}/taskinfo?taskID={}'.format(self._koji_config['weburl'], self.task_id)


class PackageGroupRelation(Base):
    group_id = Column(Integer, ForeignKey('package_group.id',
                                          ondelete='CASCADE'),
                      primary_key=True)  # there should be index on whole PK
    base_id = Column(Integer, ForeignKey('base_package.id', ondelete='CASCADE'),
                     primary_key=True, index=True)


class GroupACL(Base):
    group_id = Column(Integer, ForeignKey('package_group.id',
                                          ondelete='CASCADE'),
                      primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id',
                                         ondelete='CASCADE'),
                     primary_key=True)


class PackageGroup(Base):
    id = Column(Integer, primary_key=True)
    namespace = Column(String)
    name = Column(String, nullable=False)

    owners = relationship(User, secondary=GroupACL.__table__,
                          order_by=User.name, passive_deletes=True)

    @property
    def full_name(self):
        if self.namespace:
            return self.namespace + '/' + self.name
        return self.name

    @staticmethod
    def parse_name(name):
        if '/' not in name:
            return None, name
        ns, _, name = name.partition('/')
        return ns, name

    def __str__(self):
        return self.full_name


class Build(Base):
    __table_args__ = (
        CheckConstraint('state IN (2, 3, 5)', name='build_state_check'),
        CheckConstraint('state = 2 OR repo_id IS NOT NULL', name='build_repo_id_check'),
        CheckConstraint('state = 2 OR version IS NOT NULL', name='build_version_check'),
        CheckConstraint('state = 2 OR release IS NOT NULL', name='build_release_check'),
        CheckConstraint('NOT real OR state <> 2', name='build_real_complete_check'),
    )

    STATE_MAP = {'running': 2,
                 'complete': 3,
                 'failed': 5}
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    FINISHED_STATES = [COMPLETE, FAILED]
    STATES = [RUNNING] + FINISHED_STATES

    KOJI_STATE_MAP = {'CLOSED': COMPLETE,
                      'FAILED': FAILED}

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id', ondelete='CASCADE'))
    package = None  # backref
    state = Column(Integer, nullable=False, default=RUNNING)
    task_id = Column(Integer, nullable=False)
    started = Column(DateTime, nullable=False)
    finished = Column(DateTime)
    epoch = Column(Integer)
    version = Column(String)
    release = Column(String)
    repo_id = Column(Integer)
    # whether this build is the last complete build for corresponding package
    last_complete = Column(Boolean, nullable=False, default=False)

    cancel_requested = Column(Boolean, nullable=False, server_default=false())

    # deps_resolved is null before the build resolution is attempted
    deps_resolved = Column(Boolean)

    build_arch_tasks = relationship(KojiTask, backref='build',
                                    order_by=KojiTask.arch,
                                    passive_deletes=True)
    # was the build done by koschei or was it real build done by packager
    real = Column(Boolean, nullable=False, server_default=false())

    # was the build untagged/deleted on Koji
    untagged = Column(Boolean, nullable=False, server_default=false())

    dependency_keys = deferred(Column(CompressedKeyArray))

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
        if self.real:
            koji_config = get_config('secondary_koji_config')
        else:
            koji_config = get_config('koji_config')
        return '{}/taskinfo?taskID={}'.format(koji_config['weburl'], self.task_id)

    def __repr__(self):
        # pylint: disable=W1306
        try:
            return (
                'Build(id={b.id}, package={b.package.name}, '
                'collection={b.package.collection.name}, state={b.state_string}, '
                'task_id={b.task_id})'
            ).format(b=self)
        except Exception:
            return 'Build(id={b.id}, incomplete...)'.format(b=self)


class ResolutionChange(Base):
    id = Column(Integer, primary_key=True)
    resolved = Column(Boolean, nullable=False)
    timestamp = Column(DateTime, nullable=False, server_default=func.clock_timestamp())
    package_id = Column(
        Integer,
        ForeignKey(Package.id, ondelete='CASCADE'),
        nullable=False,
        index=True,
    )


class ResolutionProblem(Base):
    id = Column(Integer, primary_key=True)
    resolution_id = Column(
        Integer,
        ForeignKey(ResolutionChange.id, ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    problem = Column(String, nullable=False)

    def __str__(self):
        return self.problem


class Dependency(Base):
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    epoch = Column(Integer)
    version = Column(String, nullable=False)
    release = Column(String, nullable=False)
    arch = Column(String, nullable=False)

    evr = composite(
        RpmEVR, epoch, version, release,
        comparator_factory=RpmEVRComparator,
    )

    nevr = (name, epoch, version, release)
    nevra = (name, epoch, version, release, arch)
    inevra = (id, name, epoch, version, release, arch)


class AppliedChange(Base):
    __table_args__ = (
        CheckConstraint(
            'COALESCE(prev_dep_id, 0) <> COALESCE(curr_dep_id, 0)',
            name='applied_change_dep_id_check'
        ),
    )

    id = Column(Integer, primary_key=True)
    build_id = Column(
        ForeignKey('build.id', ondelete='CASCADE'),
        index=True,
        nullable=False,
    )
    prev_dep_id = Column(Integer, ForeignKey('dependency.id'), index=True)
    prev_dep = relationship(
        Dependency,
        foreign_keys=prev_dep_id,
        uselist=False,
        lazy='joined',
    )
    curr_dep_id = Column(Integer, ForeignKey('dependency.id'), index=True)
    curr_dep = relationship(
        Dependency,
        foreign_keys=curr_dep_id,
        uselist=False,
        lazy='joined',
    )
    distance = Column(Integer)
    build = None  # backref

    @property
    def dep_name(self):
        return self.curr_dep.name if self.curr_dep else self.prev_dep.name

    @property
    def prev_evr(self):
        return self.prev_dep.evr if self.prev_dep else None

    @property
    def curr_evr(self):
        return self.curr_dep.evr if self.curr_dep else None

    @property
    def package(self):
        return self.build.package


class UnappliedChange(Base):
    id = Column(Integer, primary_key=True)
    dep_name = Column(String, nullable=False)
    prev_epoch = Column(Integer)
    prev_version = Column(String)
    prev_release = Column(String)
    curr_epoch = Column(Integer)
    curr_version = Column(String)
    curr_release = Column(String)
    distance = Column(Integer)

    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'),
                        index=True, nullable=False)
    _prev_evr = composite(
        RpmEVR,
        prev_epoch, prev_version, prev_release,
        comparator_factory=RpmEVRComparator,
    )

    @hybrid_property
    def prev_evr(self):
        return self._prev_evr if self.prev_version else None

    _curr_evr = composite(
        RpmEVR,
        curr_epoch, curr_version, curr_release,
        comparator_factory=RpmEVRComparator,
    )

    @hybrid_property
    def curr_evr(self):
        return self._curr_evr if self.curr_version else None


class BuildrootProblem(Base):
    id = Column(Integer, primary_key=True)
    collection_id = Column(ForeignKey(Collection.id, ondelete='CASCADE'), index=True)
    problem = Column(String, nullable=False)


class AdminNotice(Base):
    key = Column(String, primary_key=True)
    content = Column(String, nullable=False)


class LogEntry(Base):
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL or environment = 'backend'",
            name='log_entry_user_id_check',
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey('user.id', ondelete='CASCADE'),
        nullable=True,
    )
    user = relationship('User')
    environment = Column(
        Enum('admin', 'backend', 'frontend', name='log_environment'),
        nullable=False,
    )
    timestamp = Column(DateTime, nullable=False,
                       server_default=func.clock_timestamp())
    message = Column(String, nullable=False)
    base_id = Column(
        Integer,
        ForeignKey('base_package.id', ondelete='CASCADE'),
        nullable=True,
    )


class RepoMapping(Base):
    secondary_id = Column(Integer, primary_key=True)  # repo_id on secondary
    primary_id = Column(Integer)  # repo_id on primary, not known at the beginning
    task_id = Column(Integer, nullable=False)  # newRepo task ID


class CoprRebuildRequest(Base):
    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
    )
    collection_id = Column(
        Integer,
        ForeignKey('collection.id', ondelete='CASCADE'),
        nullable=False,
    )
    repo_source = Column(String, nullable=False)
    yum_repo = Column(String)  # set by resolver to raw yum repo path
    timestamp = Column(DateTime, nullable=False,
                       server_default=func.clock_timestamp())
    description = Column(String)
    repo_id = Column(Integer)
    # how many builds should be scheduled. User can bump this value
    schedule_count = Column(Integer)
    scheduler_queue_index = Column(Integer)

    state = Column(Enum(
        'new',  # just submitted by user
        'in progress',  # resolved, scheduling in progress
        'scheduled',  # every build was scheduled
        'finished',  # every build completed
        'failed',  # error occured, processing stopped
        name='rebuild_request_state',
    ), nullable=False, server_default='new')
    error = Column(String)

    def __str__(self):
        return 'copr-request-{}'.format(self.id)


class CoprResolutionChange(Base):
    request_id = Column(
        Integer,
        ForeignKey('copr_rebuild_request.id', ondelete='CASCADE'),
        primary_key=True,
    )
    package_id = Column(
        Integer,
        ForeignKey('package.id', ondelete='CASCADE'),
        primary_key=True,
    )
    prev_resolved = Column(Boolean, nullable=False)
    curr_resolved = Column(Boolean, nullable=False)
    problems = Column(ARRAY(String))


class CoprRebuild(Base):
    # TODO migration
    __table_args__ = (
        UniqueConstraint('request_id', 'package_id', 'order',
                         name='copr_rebuild_order'),
        CheckConstraint('state IS NULL OR copr_build_id IS NOT NULL',
                        name='copr_rebuild_scheduled_build_has_copr_id_check'),
        CheckConstraint('state BETWEEN 2 AND 5',
                        name='copr_rebuild_state_check'),
    )

    request_id = Column(
        Integer,
        ForeignKey('copr_rebuild_request.id', ondelete='CASCADE'),
        primary_key=True,
    )
    package_id = Column(
        Integer,
        ForeignKey('package.id', ondelete='CASCADE'),
        primary_key=True,
    )
    copr_build_id = Column(Integer)
    prev_state = Column(Integer, nullable=False)
    state = Column(Integer)
    approved = Column(Boolean)  # TODO what was it again?
    # set by resolver, can be altered by frontend
    order = Column(Integer, nullable=False)

    @property
    def copr_name(self):
        return '{}-{}-{}'.format(
            get_config('copr.name_prefix'),
            self.request_id,
            self.package_id,
        )


def count_query(entity):
    return select([func.count(entity.id)]).select_from(entity)


class ScalarStats(MaterializedView):
    view = select((
        func.now().label('refresh_time'),
        count_query(Package).label('packages'),
        count_query(Package).where(Package.tracked).label('tracked_packages'),
        count_query(Package).where(Package.blocked).label('blocked_packages'),
        count_query(Build).label('builds'),
        count_query(Build).where(Build.real).label('real_builds'),
        count_query(Build).where(~Build.real).label('scratch_builds'),
    ))
    refresh_time = Column(DateTime, primary_key=True)
    packages = Column(Integer, nullable=False)
    tracked_packages = Column(Integer, nullable=False)
    blocked_packages = Column(Integer, nullable=False)
    builds = Column(Integer, nullable=False)
    real_builds = Column(Integer, nullable=False)
    scratch_builds = Column(Integer, nullable=False)


def _resource_consumption_stats_view():
    time_difference_expr = func.sum(KojiTask.finished - KojiTask.started)
    time_difference = extract('EPOCH', time_difference_expr)
    time_difference_all = select([time_difference]).select_from(KojiTask)
    return (
        select([
            Package.name,
            KojiTask.arch,
            time_difference_expr.label('time'),
            cast(time_difference / time_difference_all, Float).label('time_percentage'),
        ])
        .select_from(
            join(
                join(Package, Build, Package.id == Build.package_id),
                KojiTask,
            )
        )
        .group_by(Package.name, KojiTask.arch)
    )


class ResourceConsumptionStats(MaterializedView):
    view = _resource_consumption_stats_view()
    name = Column(String, primary_key=True)
    arch = Column(String, primary_key=True)
    time = Column(Interval, index=True)
    time_percentage = Column(Float)


# Indices
Index('ix_build_composite', Build.package_id, Build.started.desc())
Index('ix_package_group_name', PackageGroup.namespace, PackageGroup.name,
      unique=True)
Index('ix_dependency_composite', *Dependency.nevra, unique=True)
Index('ix_package_collection_id', Package.collection_id, Package.tracked,
      postgresql_where=(~Package.blocked))
Index('ix_builds_unprocessed', Build.task_id,
      postgresql_where=(Build.deps_resolved.is_(None) & Build.repo_id.isnot(None)))
Index('ix_builds_last_complete', Build.package_id, Build.task_id,
      postgresql_where=(Build.last_complete))


# Relationships
Package.last_complete_build = relationship(
    Build,
    primaryjoin=(Build.id == Package.last_complete_build_id),
    uselist=False,
)
Package.last_build = relationship(
    Build,
    primaryjoin=(Build.id == Package.last_build_id),
    uselist=False,
)
Package.all_builds = relationship(
    Build,
    order_by=Build.started.desc(),
    primaryjoin=(Build.package_id == Package.id),
    backref='package',
    passive_deletes=True,
)
Package.unapplied_changes = relationship(
    UnappliedChange,
    backref='package',
    order_by=[UnappliedChange.distance, UnappliedChange.dep_name],
)
Build.dependency_changes = relationship(
    AppliedChange,
    backref='build',
    primaryjoin=(Build.id == AppliedChange.build_id),
    order_by=AppliedChange.distance.nullslast(),
    passive_deletes=True,
)
ResolutionChange.problems = relationship(
    ResolutionProblem,
    backref='result',
    passive_deletes=True,
)
PackageGroup.package_count = column_property(
    select([func.count()],
           PackageGroupRelation.group_id == PackageGroup.id,
           join(BasePackage, PackageGroupRelation,
                PackageGroupRelation.base_id == BasePackage.id))
    .where(~BasePackage.all_blocked)
    .correlate(PackageGroup).as_scalar(),
    deferred=True)
# pylint: disable=E1101
BasePackage.groups = relationship(
    PackageGroup,
    secondary=PackageGroupRelation.__table__,
    secondaryjoin=(PackageGroup.id == PackageGroupRelation.group_id),
    primaryjoin=(PackageGroupRelation.base_id == BasePackage.id),
    order_by=PackageGroup.name,
    passive_deletes=True,
)
Package.groups = relationship(
    PackageGroup,
    secondary=PackageGroupRelation.__table__,
    secondaryjoin=(PackageGroup.id == PackageGroupRelation.group_id),
    primaryjoin=(PackageGroupRelation.base_id == Package.base_id),
    order_by=PackageGroup.name,
    passive_deletes=True,
)
PackageGroup.packages = relationship(
    BasePackage,
    secondary=PackageGroupRelation.__table__,
    primaryjoin=(PackageGroup.id == PackageGroupRelation.group_id),
    secondaryjoin=(PackageGroupRelation.base_id == BasePackage.id),
    order_by=BasePackage.name,
    passive_deletes=True,
)
PackageGroupRelation.group = relationship(PackageGroup)
User.groups = relationship(
    PackageGroup,
    secondary=GroupACL.__table__,
    order_by=[PackageGroup.namespace, PackageGroup.name],
    passive_deletes=True,
)
CollectionGroup.collections = relationship(
    Collection,
    secondary=CollectionGroupRelation.__table__,
    order_by=(Collection.order.desc(), Collection.name.desc()),
    passive_deletes=True,
)
CoprRebuildRequest.collection = relationship(Collection)
CoprRebuildRequest.resolution_changes = relationship(CoprResolutionChange)
CoprRebuildRequest.rebuilds = relationship(
    CoprRebuild,
    order_by=CoprRebuild.order,
)
CoprRebuildRequest.user = relationship(User)
CoprRebuild.package = relationship(Package)
CoprRebuild.request = relationship(CoprRebuildRequest)
CoprResolutionChange.package = relationship(Package)

configure_mappers()
