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

"""
Database schema definitions for Koschei.
Tables are defined first. Indices and relationships are typically defined separately later
in this file. DB utility functions belong to `koschei.db` module, not here.
Frontend monkey-patches additional properties to the models in
`koschei.frontend.model_additions`.
"""

import math

from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey, DateTime, Index, Float,
    CheckConstraint, UniqueConstraint, Enum, Interval,
)
from sqlalchemy.sql.expression import (
    func, select, join, false, true, extract, case, null, cast,
)
from sqlalchemy.orm import (
    relationship, column_property, configure_mappers, deferred, composite,
)
from sqlalchemy.dialects.postgresql import ARRAY

from .config import get_config
from koschei.db import (
    Base, MaterializedView, CompressedKeyArray, RpmEVR, RpmEVRComparator,
    sql_property,
)


class User(Base):
    """
    User model used for:
    - authentication and authorization
    - querying user's packages (by user name)
    - logging user actions

    Note: existence of a User entry does not imply existence of a corresponding user in
          the authentication system. For example, when adding group maintainers,
          the User entry is created without checking for existence of the user.

    Regular users can:
    - edit packages (make tracked, set manual priority...)
    - create groups in their namespace
    - edit groups where marked as maintainers
    - get a convenience "My packages" link, although anyone can get to the same page by
      constructing the right URL

    Admins can:
    - edit all groups
    - cancel builds
    """
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    admin = Column(Boolean, nullable=False, server_default=false())


class Collection(Base):
    """
    A collection of packages with the same Koji configuration. In Fedora, it corresponds
    to a Fedora release. Nearly everything in Koschei is grouped by collections.
    Collections are created/edited using `koschei-admin` script.

    Collections can be setup in two modes.
    Primary mode is the default, where a single Koji instance is used for everything.
    Secondary mode uses two Koji instances. One "primary" for performing the
    scratch-builds and one "secondary" which is used read-only (unauthenticated) as
    a source of repos, srpms, real builds and other data.
    """
    __table_args__ = (
        CheckConstraint(
            '(latest_repo_resolved IS NULL) = (latest_repo_id IS NULL)',
            name='collection_latest_repo_id_check',
        ),
    )

    id = Column(Integer, primary_key=True)
    order = Column(Integer, nullable=False, server_default="100")
    # name used in machine context (urls, fedmsg), e.g. "f24"
    name = Column(String, nullable=False, unique=True)
    # name for ordinary people, e.g. "Fedora 24". Used in frontend, log messages
    # and fedmsg (contains both names)
    display_name = Column(String, nullable=False)

    # whether this collection is in secondary or primary mode
    secondary_mode = Column(Boolean, nullable=False, server_default=false())

    # Koji configuration
    # Koji target for scratch-builds
    target = Column(String, nullable=False)
    # Koji tag used for querying packages and real builds. Usually set to the same value
    # as `build_tag` (done by default by `koschei-admin` script).
    dest_tag = Column(String, nullable=False)
    # Koji build tag of given target. Set automatically by `koschei-admin` when creating
    # or editing the collection
    build_tag = Column(String, nullable=False)

    # Two fields interpolated into bugreport template. If null, bug filling will be
    # disabled for this collection
    bugzilla_product = Column(String)
    bugzilla_version = Column(String)

    # Priority of all packages in this collection is multiplied by this value.
    # Can be used to deprioritize collections for older releases by setting it to a value
    # less than 1
    priority_coefficient = Column(Float, nullable=False, server_default='1')

    # Koji build group name (in comps) for base buildroot. Used by resolvers.
    build_group = Column(String, nullable=False, server_default='build')

    # Two fields marking the latest repo for this collection which was successfully
    # resolved by `repo_resolver`. Sucessfully resolved means that build group was
    # installable (but resolving packages may still be in progress).
    # Setting both to null can be used to force resolution even if there's no repo.
    # Used by:
    # - repo_resolver to determine whether there is a newer repo
    # - copr plugin to get baseline repo
    # - repo_regen plugin to mirror the repo on primary
    # - frontend to warn users when base buildroot is not installable
    # The repo_id is Koji's repo ID
    latest_repo_id = Column(Integer)
    latest_repo_resolved = Column(Boolean)

    # whether to poll builds also for untracked packages
    poll_untracked = Column(Boolean, nullable=False, server_default=true())

    # all package in the collection
    packages = relationship('Package', backref='collection', passive_deletes=True)

    @property
    def state_string(self):
        """
        :return: machine readable name of the collection state
        """
        return {
            True: 'ok',
            False: 'unresolved',
            None: 'unknown',
        }[self.latest_repo_resolved]

    def __str__(self):
        return self.display_name


class CollectionGroup(Base):
    """
    Grouping of collections (many-to-many).
    Used only by frontend to show collections in groups, such as "Fedora".
    """
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=False)

    def __str__(self):
        return self.display_name


class CollectionGroupRelation(Base):
    """
    Relationship table for collection grouping.
    """
    group_id = Column(
        ForeignKey('collection_group.id', ondelete='CASCADE'),
        primary_key=True
    )
    collection_id = Column(
        ForeignKey('collection.id', ondelete='CASCADE'),
        primary_key=True,
    )


class BasePackage(Base):
    """
    Common data about a package not specific to a particular collection.
    For example the package name or being part of a group.
    """
    id = Column(Integer, primary_key=True)
    # Source package name (SRPM %{name})
    name = Column(String, nullable=False, unique=True)
    # All collection-specific Package entities for this package
    packages = relationship('Package', backref='base', passive_deletes=True)

    # Updated automatically by a trigger, used by frontend to speed up some queries
    all_blocked = Column(Boolean, nullable=False, server_default=true())


class TimePriority(object):
    """
    Container for lazy computation of static time priority inputs.
    Wrapped in a method because we cannot read config values during top-level execution
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
    """
    Package data specific to particular collection (+ denormalized fields). Nonspecific
    fields are in BasePackage.
    """
    __table_args__ = (
        UniqueConstraint('base_id', 'collection_id',
                         name='package_unique_in_collection'),
        CheckConstraint('NOT skip_resolution OR resolved IS NULL',
                        name='package_skip_resolution_check'),
    )

    id = Column(Integer, primary_key=True)
    base_id = Column(
        ForeignKey(BasePackage.id, ondelete='CASCADE'),
        nullable=False,
    )
    # denormalized name from base_package
    name = Column(String, nullable=False, index=True)
    collection_id = Column(
        ForeignKey(Collection.id, ondelete='CASCADE'),
        nullable=False,
    )
    collection = None  # backref, shut up pylint

    # Used to override build architectures for scratch-builds. Can be set by normal users
    # via frontend. See scheduler/koji_util for how exactly the overriding works.
    arch_override = Column(String)
    # Do not ever try to resolve the package. Package then has resolved == None, but it is
    # still considered resolved by scheduler or frontend.
    skip_resolution = Column(Boolean, nullable=False, server_default=false())

    # denormalized fields, updated by trigger on insert/update (no delete)
    last_complete_build_id = Column(
        ForeignKey(
            'build.id',
            use_alter=True,
            name='fkey_package_last_complete_build_id',
        ),
        nullable=True,
    )
    last_complete_build_state = Column(Integer)
    last_build_id = Column(
        ForeignKey(
            'build.id',
            use_alter=True,
            name='fkey_package_last_build_id',
            # it's first updated by trigger, this is fallback, when there's
            # nothing to update it to
            ondelete='SET NULL',
        ),
        nullable=True,
    )
    # Whether all package dependencies were installable suring latest repo_resolver run.
    # May be None if resolution was not attempted yet.
    # When False, installation problems are stored in ResolutionProblem table.
    resolved = Column(Boolean)

    # priority calculation input values
    # priority set by (super)user, never reset by koschei
    static_priority = Column(Integer, nullable=False, server_default='0')
    # priority set by any user, reset to 0 after a build is submitted/registered
    manual_priority = Column(Integer, nullable=False, server_default='0')
    # priority based on builds: build started to fail, build failed to resolve,
    # last build wasn't resolved and thus new build is necessary
    build_priority = Column(Integer, nullable=False, server_default='0')
    # priority based on dependency changes since last build
    dependency_priority = Column(Integer, nullable=False, server_default='0')

    # Whether Koschei "tracks" a package. It means that builds are scheduled for the
    # package and it is shown in the frontend and its dependencies are periodically tested
    # for installability.
    # Untracked packages are still present in the DB, they can be shown in the frontend
    # when user explicitly asks for them, their builds are polled depending on
    # `poll_untracked` attribute of the collection. They are not resolved and no builds
    # submitted for them.
    tracked = Column(Boolean, nullable=False, server_default=false())
    # Whether the package is blocked in Koji. Blocked packages should not be considered by
    # anything in Koschei. They're kept for the case if they become unblocked.
    blocked = Column(Boolean, nullable=False, server_default=false())

    # Scheduler can mark why a particular package was not schedulable
    SKIPPED_NO_SRPM = 1  # there was no SRPM found
    SKIPPED_NO_ARCH = 2  # package cannot be built on any of the arches allowed by config
    scheduler_skip_reason = Column(Integer)

    @classmethod
    def current_priority_expression(cls, collection, last_build):
        """
        Return computed value for packages priority or None if package is not
        schedulable.

        :param: collection package's collection.
                           It should either be the Collection class object,
                           or it should be a concrete collection object if all packages
                           have the same collection. This is done as an optimization to
                           avoid adding join on collection table when the collection is
                           known.
        :param: last_build package's last complete build.
                           As with the previous argument, should be either Build class
                           object or particular last complete build object.

        :returns: SQLA expression that, when evaluated in the DB, returns the priority
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
        """
        :return: A list of human-readable descriptions of why the package is not
                 schedulable. Empty if the package is schedulable.
        """
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
        """
        String representation of state used when displaying to user. Also used for as a
        key for CSS class names or icon names.

        :return: A string value if called on an instance. A SQLA expression when called
                 on the class.
        """
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
        """
        String representation of state used when publishing fedmsg messages. Kept distinct
        from `state_string` for backwards-compatibility.
        """
        state = self.state_string
        return state if state in ('ok', 'failing', 'unresolved') else 'ignored'

    @property
    def has_running_build(self):
        return self.last_build_id != self.last_complete_build_id

    @property
    def srpm_nvra(self):
        """
        :return: name-version-release-arch computed from last complete build in dictionary
                 form. May be None if there's no build.
        """
        return dict(name=self.name,
                    version=self.last_complete_build.version,
                    release=self.last_complete_build.release,
                    arch='src') if self.last_complete_build else None

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)


class KojiTask(Base):
    """
    A Koji `buildArch` subtask of the `build` task. A Build has many KojiTasks.
    Usually there's a single task for `noarch` builds and tasks for each arch for archful
    builds.
    """
    __table_args__ = (
        CheckConstraint('state BETWEEN 0 AND 5', name='koji_task_state_check'),
    )

    id = Column(Integer, primary_key=True)
    build_id = Column(
        ForeignKey('build.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # Koji task ID
    task_id = Column(Integer, nullable=False)
    # Architecture in Koji's format
    arch = Column(String, nullable=False)
    # Koji's TASK_STATE id
    state = Column(Integer, nullable=False)
    # Time of task start
    started = Column(DateTime, nullable=False)
    # Time of task finish. May be None. Unused.
    finished = Column(DateTime)

    @property
    def state_string(self):
        """
        :return: String representation of the task state. Used to lookup CSS classes.
        """
        # return [state for state, num in koji.TASK_STATES.items()
        #         if num == self.state][0].lower()
        # pylint:disable=invalid-sequence-index
        states = ['free', 'open', 'closed', 'canceled', 'assigned', 'failed']
        return states[self.state]

    @property
    def results_url(self):
        """
        :return: Absolute URL to Koji task results (logs) for this task
        """
        # pathinfo = koji.PathInfo(topdir=self._koji_config['topurl'])
        # return pathinfo.task(self.task_id)
        return '{}/work/tasks/{}/{}'.format(
            self.build.koji_config['topurl'],
            self.task_id % 10000,
            self.task_id,
        )

    @property
    def taskinfo_url(self):
        """
         :return: Absolute URL to Koji task info page
         """
        return '{}/taskinfo?taskID={}'.format(
            self.build.koji_config['weburl'],
            self.task_id,
        )


class PackageGroupRelation(Base):
    """
    Relation table between PackageBase and PackageGroup.
    """
    group_id = Column(
        ForeignKey('package_group.id', ondelete='CASCADE'),
        primary_key=True,  # there should be index on whole PK
    )
    base_id = Column(
        ForeignKey('base_package.id', ondelete='CASCADE'),
        primary_key=True,
        index=True,
    )


class GroupACL(Base):
    """
    An entry of PackageGroup's maintainer list.
    """
    group_id = Column(
        ForeignKey('package_group.id', ondelete='CASCADE'),
        primary_key=True,
    )
    user_id = Column(
        ForeignKey('user.id', ondelete='CASCADE'),
        primary_key=True,
    )


class PackageGroup(Base):
    """
    Grouping of packages (many-to-many). Created and maintained by users. Has a list of
    maintainers that can edit the group. Groups are namespaced by username by default,
    but the namespace can be set to null (using `koschei-admin` only), in which case it is
    displayed as global. Refers to BasePackage (not Package), so it's not
    collection-specific.
    """
    id = Column(Integer, primary_key=True)
    # A namespace name. By default set to creator's username. Can be modified using
    # `koschei-admin` only. Groups with null namespace are global groups.
    namespace = Column(String)
    # Group name
    name = Column(String, nullable=False)

    # List of people who can edit/delete the group. They can add other owners. Admins can
    # edit all groups. Frontend shows "My groups" to a logged in user based on ownership.
    owners = relationship(
        User,
        secondary=GroupACL.__table__,
        order_by=User.name,
        passive_deletes=True,
    )

    @property
    def full_name(self):
        """
        :return: Qualified name in namespace/name format. Or just name for global groups.
        """
        if self.namespace:
            return self.namespace + '/' + self.name
        return self.name

    @staticmethod
    def parse_name(name):
        """
        Inverse of `full_name`.
        :param name Qualified name in namespace/name format. Or just name for global
                    groups.
        :return: namespace, name pair. Namespace will be None for a global group.
        """
        if '/' not in name:
            return None, name
        ns, _, name = name.partition('/')
        return ns, name

    def __str__(self):
        return self.full_name


class Build(Base):
    """
    A single build. Used both for builds submitted by Koschei and also builds done as
    regular (not scratch) builds done by package maintainers. The latter builds are called
    "real" builds.

    Canceled builds are deleted.
    Old builds can be deleted by `koschei-admin cleanup` (run from cron).
    """
    __table_args__ = (
        CheckConstraint('state IN (2, 3, 5)', name='build_state_check'),
        CheckConstraint('state = 2 OR repo_id IS NOT NULL', name='build_repo_id_check'),
        CheckConstraint('state = 2 OR version IS NOT NULL', name='build_version_check'),
        CheckConstraint('state = 2 OR release IS NOT NULL', name='build_release_check'),
        CheckConstraint('NOT real OR state <> 2', name='build_real_complete_check'),
    )

    STATE_MAP = {
        'running': 2,
        'complete': 3,
        'failed': 5,
    }
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    FINISHED_STATES = [COMPLETE, FAILED]
    STATES = [RUNNING] + FINISHED_STATES

    KOJI_STATE_MAP = {
        'CLOSED': COMPLETE,
        'FAILED': FAILED,
    }

    id = Column(Integer, primary_key=True)
    package_id = Column(ForeignKey('package.id', ondelete='CASCADE'))
    package = None  # backref
    # Build state as an integer. Can be either 2 (running), 3 (complete) or 5 (failed).
    state = Column(Integer, nullable=False, default=RUNNING)
    # Koji task ID
    task_id = Column(Integer, nullable=False)
    # Task creation time. Used for ordering builds and relating them to ResolutionChanges
    started = Column(DateTime, nullable=False)
    # Task finish time. May be null
    finished = Column(DateTime)

    # RPM version components: epoch, version and release. Epoch may be null, should be
    # treated same as 0
    epoch = Column(Integer)
    version = Column(String)
    release = Column(String)

    # Koji repo ID in which the build was done. Used to get the repo for dependency
    # resolution
    repo_id = Column(Integer)
    # Whether this build is the last complete build for corresponding package.
    # Used as an optimization to speed up certain queries.
    # Populated by a DB trigger.
    last_complete = Column(Boolean, nullable=False, default=False)

    # Whether admin requested cancelation via frontend
    cancel_requested = Column(Boolean, nullable=False, server_default=false())

    # Whether all dependencies were installable in the same repo as the build was done.
    # Is null before the build resolution is attempted.
    deps_resolved = Column(Boolean)

    # Koji `buildArch` tasks
    build_arch_tasks = relationship(
        KojiTask,
        backref='build',
        order_by=KojiTask.arch,
        passive_deletes=True,
    )
    # Was the build done by koschei (False) or was it real build done by packager (True)
    # Real builds are displayed differently in the frontend.
    # In secondary mode, real builds are done on secondary Koji, whereas normal
    # scratch-builds are done on primary Koji
    real = Column(Boolean, nullable=False, server_default=false())

    # Was the build untagged/deleted on Koji
    # Untagged builds are displayed differently in the frontend and are ignored by
    # resolvers and scheduler
    untagged = Column(Boolean, nullable=False, server_default=false())

    # List of IDs of dependencies in the Dependency table. Stored as a compressed
    # byte-array for space-saving reasons, but the code can use it as normal list of
    # integers, thanks to custom SQLA type.
    # Stored only if the build is the last complete, otherwise set to null to save space.
    # Used only by resolver. Deferred = not fetched from DB by default.
    dependency_keys = deferred(Column(CompressedKeyArray))

    @property
    def state_string(self):
        """
        :return: String representation of the build state. Displayed to the user and used
                 to lookup CSS classes and icons.
        """
        return self.REV_STATE_MAP[self.state]

    @property
    def srpm_nvra(self):
        """
        :return: name-version-release-arch computed from last complete build in dictionary
                 form.
        """
        # pylint:disable=no-member
        return {
            'name': self.package.name,
            'version': self.version,
            'release': self.release,
            'arch': 'src',
        }

    @property
    def koji_config(self):
        """
        :return: Koschei's Koji config dict for corresponding instance.
        """
        # pylint:disable=no-member
        if self.real and self.package.collection.secondary_mode:
            return get_config('secondary_koji_config')
        return get_config('koji_config')

    @property
    def taskinfo_url(self):
        """
        :return: Absolute URL to Koji task info page for the build's main task
        """
        return '{}/taskinfo?taskID={}'.format(self.koji_config['weburl'], self.task_id)

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
    """
    An entry representing that a particular package's reslution state (whether its
    dependencies were installable) has changed at given point of time. Used only the
    frontend to show past changes. The use-case is that without this feature, people
    often saw a fedmsg that a package failed to resolve, but by the time they opened
    Koschei, it had already been resolved again and they had no idea what had been wrong.
    """
    id = Column(Integer, primary_key=True)
    # Whether package's dependencies were installable or not
    resolved = Column(Boolean, nullable=False)
    # Timestamp of the resolution, used to order them and relate them to builds
    timestamp = Column(DateTime, nullable=False, server_default=func.clock_timestamp())
    package_id = Column(
        ForeignKey(Package.id, ondelete='CASCADE'),
        nullable=False,
        index=True,
    )


class ResolutionProblem(Base):
    """
    A string representation of a problem in dependency installation. Produced by resolver
    straight from hawkey/libdnf output.
    """
    id = Column(Integer, primary_key=True)
    resolution_id = Column(
        ForeignKey(ResolutionChange.id, ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    problem = Column(String, nullable=False)

    def __str__(self):
        return self.problem


class Dependency(Base):
    """
    A binary RPM name-epoch-version-release-arch. Kind of a flyweight pattern to avoid
    storing the same NEVRAs multiple times as they consume a lot space.
    Each NEVRA must be unique (there's a unique index defined later).
    Referenced by Build.dependency_keys and Applied/UnappliedChange.prev/curr_dep_id
    """
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    epoch = Column(Integer)
    version = Column(String, nullable=False)
    release = Column(String, nullable=False)
    arch = Column(String, nullable=False)

    # Composite property that can be used for queries that need to compare EVRs
    # RPM's way
    evr = composite(
        RpmEVR, epoch, version, release,
        comparator_factory=RpmEVRComparator,
    )

    nevr = (name, epoch, version, release)
    nevra = (name, epoch, version, release, arch)
    inevra = (id, name, epoch, version, release, arch)


class AppliedChange(Base):
    """
    Representation of a change in installed dependencies between the build referenced by
    it and the previous one. Dependency changes are caused by changes in the repo or in
    package's BuildRequires (real builds only). Represented as a row with EVR of a
    dependency of the previous build (prev_evr) and EVR of the same dependency of the
    current build. The dependencies must have the same name. The EVRs must be different.
    Either of them (but not both at the same time) can be null to signify that a new
    dependency appeared or disappeared.

    Dependency changes have a distance computed as a distance of the dependency in the
    dependency graph from the package. Direct BuildRequires have distance 1. Indirect
    dependencies have distance > 1 and < 8 (arbitrary max depth, may change). Distance may
    be null, which means that it is either a build group dependency, too distant
    dependency or another transaction dependency (scriptlet).

    Generated by build_resolver for each build. Displayed by frontend.
    """
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
    prev_dep_id = Column(ForeignKey('dependency.id'), index=True)
    prev_dep = relationship(
        Dependency,
        foreign_keys=prev_dep_id,
        uselist=False,
        lazy='joined',
    )
    curr_dep_id = Column(ForeignKey('dependency.id'), index=True)
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
    """
    The same type of dependency change as in AppliedChange, but the difference is done
    between package's last complete build and package's current set of dependencies.
    It references the package instead of the build.

    Unapplied changes are used to compute depedency priority used as the main scheduling
    criteria. This computation happens only once when they're generated.

    Generated by repo_resolver. Displayed by frontend.
    """
    __table_args__ = (
        CheckConstraint(
            'COALESCE(prev_dep_id, 0) <> COALESCE(curr_dep_id, 0)',
            name='unapplied_change_dep_id_check'
        ),
    )
    id = Column(Integer, primary_key=True)
    package_id = Column(
        ForeignKey('package.id', ondelete='CASCADE'),
        index=True,
        nullable=False,
    )
    prev_dep_id = Column(ForeignKey('dependency.id'), index=True)
    prev_dep = relationship(
        Dependency,
        foreign_keys=prev_dep_id,
        uselist=False,
        lazy='joined',
    )
    curr_dep_id = Column(ForeignKey('dependency.id'), index=True)
    curr_dep = relationship(
        Dependency,
        foreign_keys=curr_dep_id,
        uselist=False,
        lazy='joined',
    )
    distance = Column(Integer)

    @property
    def dep_name(self):
        return self.curr_dep.name if self.curr_dep else self.prev_dep.name

    @property
    def prev_evr(self):
        return self.prev_dep.evr if self.prev_dep else None

    @property
    def curr_evr(self):
        return self.curr_dep.evr if self.curr_dep else None


class BuildrootProblem(Base):
    """
    Same as ResolutionProblem, but for the entire base builroot (install build group).
    The reason for the separation is that Koschei doesn't resolve individual packages
    when base buildroot is not resolvable.

    Generated by repo_resolver. Presence of buildroot problems is indicated by
    collection's latest_repo_resolved field.
    Displayed by the frontend as a global warning.
    """
    id = Column(Integer, primary_key=True)
    collection_id = Column(
        ForeignKey(Collection.id, ondelete='CASCADE'),
        index=True,
    )
    problem = Column(String, nullable=False)


class AdminNotice(Base):
    """
    Global notice shown on every page in the frontend. Used to inform about outages etc.
    Key is always "global_notice", other keys are not presently used.
    Set or cleared by koschei-admin script.
    """
    key = Column(String, primary_key=True)
    content = Column(String, nullable=False)


class LogEntry(Base):
    """
    An audit log of a particular effective action (DB modification) done, such as changing
    package's attributes by user.
    Append-only table. Currently not displayed by anything. Can be used by administrators
    manually via SQL.
    Produced mostly by frontend and koschei-admin.
    """
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL or environment = 'backend'",
            name='log_entry_user_id_check',
        ),
    )
    id = Column(Integer, primary_key=True)
    # ID of a user, may be root for koschei-admin entries, is null for backend entries
    user_id = Column(
        ForeignKey('user.id', ondelete='CASCADE'),
        nullable=True,
    )
    user = relationship('User')
    environment = Column(
        Enum('admin', 'backend', 'frontend', name='log_environment'),
        nullable=False,
    )
    timestamp = Column(
        DateTime,
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    # Readable description of the event that occured
    message = Column(String, nullable=False)
    # Package to which the action pertains, if any
    base_id = Column(
        ForeignKey('base_package.id', ondelete='CASCADE'),
        nullable=True,
    )


class RepoMapping(Base):
    """
    Used in secondary mode only.
    Maps Koji repo IDs between primary and secondary Koji. Used to convert Builds'
    repo IDs to secondary Koji repo IDs, so that resolver can process the uniformly.
    Also used by new repo check in repo_resolver.
    """
    # repo_id on secondary
    secondary_id = Column(Integer, primary_key=True)
    # repo_id on primary, not known at the beginning
    primary_id = Column(Integer)
    # newRepo task ID
    task_id = Column(Integer, nullable=False)


class CoprRebuildRequest(Base):
    """
    Used by copr plugin to represent a users request to rebuild packages with additional
    copr repo added, in order to test whether his changes break anything.
    """
    id = Column(Integer, primary_key=True)
    # Requestor
    user_id = Column(
        ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
    )
    collection_id = Column(
        ForeignKey('collection.id', ondelete='CASCADE'),
        nullable=False,
    )
    # String refering to copr used to abtain the repo
    # format: copr:owner/name or copr:name
    repo_source = Column(String, nullable=False)
    # Set by resolver to raw yum repo URL
    yum_repo = Column(String)
    # Creation time
    timestamp = Column(
        DateTime,
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    # Descirption entered by the user. Informative only
    description = Column(String)
    # Koji repo ID used to obtain the base repo, taken from colection.latest_repo_id
    # at resolution time
    repo_id = Column(Integer)
    # How many builds should be scheduled. User can bump this value
    schedule_count = Column(Integer)
    # Current cursor into the build queue
    scheduler_queue_index = Column(Integer)

    state = Column(
        Enum(
            'new',  # just submitted by user
            'in progress',  # resolved, scheduling in progress
            'scheduled',  # every build was scheduled
            'finished',  # every build completed
            'failed',  # error occured, processing stopped
            name='rebuild_request_state',
        ),
        nullable=False,
        server_default='new',
    )
    error = Column(String)

    def __str__(self):
        return 'copr-request-{}'.format(self.id)


class CoprResolutionChange(Base):
    """
    Like ResolutionChange, but for copr rebuilds. Signifies that a package failed to
    resolve with the copr repo added while it was ok without it (or the other way around).
    """
    request_id = Column(
        ForeignKey('copr_rebuild_request.id', ondelete='CASCADE'),
        primary_key=True,
    )
    package_id = Column(
        ForeignKey('package.id', ondelete='CASCADE'),
        primary_key=True,
    )
    prev_resolved = Column(Boolean, nullable=False)
    curr_resolved = Column(Boolean, nullable=False)
    problems = Column(ARRAY(String))


class CoprRebuild(Base):
    """
    A build scheduled or done by copr plugin (in copr).
    Created for every package, whose dependencies changed after adding the copr repo.
    Only first `schedule_count` builds are actually submitted.
    """
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
        ForeignKey('copr_rebuild_request.id', ondelete='CASCADE'),
        primary_key=True,
    )
    package_id = Column(
        ForeignKey('package.id', ondelete='CASCADE'),
        primary_key=True,
    )
    copr_build_id = Column(Integer)
    # State of the last complete build of the package at the time of creation of
    # this build
    prev_state = Column(Integer, nullable=False)
    # Current state
    state = Column(Integer)
    approved = Column(Boolean)  # TODO what was it again?
    # Order in the rebuild queue. Set by resolver, can be altered by frontend
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
    """
    Materialized view for statistics page. Regenerated by polling.

    Contains global statistics.
    """
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
    """
    Materialized view for statistics page. Regenerated by polling.

    Contains per-package statistics.
    """
    view = _resource_consumption_stats_view()
    name = Column(String, primary_key=True)
    arch = Column(String, primary_key=True)
    time = Column(Interval, index=True)
    time_percentage = Column(Float)


# Indices
Index(
    'ix_build_composite',
    Build.package_id,
    Build.started.desc(),
)
Index(
    'ix_package_group_name',
    PackageGroup.namespace,
    PackageGroup.name,
    unique=True,
)
Index(
    'ix_dependency_composite',
    *Dependency.nevra,
    unique=True,
)
Index(
    'ix_package_collection_id',
    Package.collection_id,
    Package.tracked,
    postgresql_where=(~Package.blocked),
)
Index(
    'ix_builds_unprocessed',
    Build.task_id,
    postgresql_where=(Build.deps_resolved.is_(None) & Build.repo_id.isnot(None)),
)
Index(
    'ix_builds_last_complete',
    Build.package_id,
    Build.task_id,
    postgresql_where=(Build.last_complete),
)


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
    order_by=UnappliedChange.distance,
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

# Finalize ORM setup, no DB entities should be defined past this
configure_mappers()
