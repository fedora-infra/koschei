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

from __future__ import print_function, absolute_import

from datetime import datetime, timedelta
from six.moves import zip as izip

import koji
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlalchemy.sql import insert

from koschei import util
from koschei.session import KoscheiSession
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.koji_util import itercall
from koschei.db import Session
from koschei.models import (Build, UnappliedChange, KojiTask, Package,
                            PackageGroup, PackageGroupRelation, BasePackage,
                            Collection, RepoMapping)
from koschei.plugin import dispatch_event


class KoscheiBackendSession(KoscheiSession):
    def __init__(self):
        super(KoscheiBackendSession, self).__init__()
        self._db = None
        self._koji_sessions = {}
        self._repo_cache = None

    @property
    def db(self):
        if self._db is None:
            self._db = Session()
        return self._db

    def koji(self, koji_id):
        """
        Returns (and creates if necessary) current koji session for given
        koji_id (primary or secondary).
        """
        if koji_id not in self._koji_sessions:
            if koji_id == 'primary':
                self._koji_sessions[koji_id] = \
                    koji_util.KojiSession(koji_id=koji_id, anonymous=False)
            elif koji_id == 'secondary':
                if get_config('secondary_mode'):
                    self._koji_sessions[koji_id] = \
                        koji_util.KojiSession(koji_id=koji_id, anonymous=True)
                else:
                    return self.koji('primary')
            else:
                raise AssertionError("Unknown koji_id: {}".format(koji_id))

        return self._koji_sessions[koji_id]

    def secondary_koji_for(self, collection):
        """
        Returns secondary session for secondary mode and primary otherwise.

        :param: collection collection object
        """
        return self.koji(
            'secondary' if collection.secondary_mode else 'primary'
        )

    @property
    def repo_cache(self):
        if self._repo_cache is None:
            from koschei.backend.repo_cache import RepoCache
            self._repo_cache = RepoCache()
        return self._repo_cache


def submit_build(session, package):
    build = Build(package_id=package.id, state=Build.RUNNING)
    name = package.name
    build_opts = {}
    if package.arch_override:
        override = package.arch_override
        if override.startswith('^'):
            excludes = override[1:].split()
            build_arches = get_config('koji_config').get('build_arches')
            includes = set(build_arches) - set(excludes)
            override = ' '.join(sorted(includes))
        build_opts = {'arch_override': override}
    # on secondary collections SRPMs are taken from secondary, primary
    # needs to be able to build from relative URL constructed against
    # secondary (internal redirect)
    srpm_res = koji_util.get_last_srpm(
        session.secondary_koji_for(package.collection),
        package.collection.dest_tag,
        name
    )
    if srpm_res:
        srpm, srpm_url = srpm_res
        # priorities are reset after the build is done
        # - the reason for that is that the build might be canceled and we want
        # the priorities to be retained in that case
        build.task_id = koji_util.koji_scratch_build(
            session.koji('primary'),
            package.collection.target,
            name,
            srpm_url,
            build_opts
        )
        build.started = datetime.now()
        build.epoch = srpm['epoch']
        build.version = srpm['version']
        build.release = srpm['release']
        session.db.add(build)
        session.db.flush()
        return build


def get_newer_build_if_exists(session, package):
    [info] = session.secondary_koji_for(package.collection)\
        .listTagged(package.collection.dest_tag, latest=True,
                    package=package.name, inherit=True) or [None]
    if info and util.is_build_newer(package.last_build, info):
        return info


def register_real_builds(session, collection, package_build_infos):
    """
    Registers real builds for given build infos.
    Takes care of concurrency and commits the transaction.

    :param: package_build_infos tuples in format (package_id, build_info)
    """
    state_map = {
        koji.BUILD_STATES['COMPLETE']: Build.COMPLETE,
        koji.BUILD_STATES['FAILED']: Build.FAILED,
    }
    # prepare ORM objects for insertion
    builds = [
        Build(
            real=True,
            state=state_map[build_info['state']],
            task_id=build_info['task_id'],
            epoch=build_info['epoch'],
            version=build_info['version'],
            release=build_info['release'],
            package_id=package_id,
        )
        for package_id, build_info in package_build_infos
    ]
    # process the input in chunks to prevent locking too many packages at once
    for chunk in util.chunks(builds, get_config('real_builds_insert_chunk')):
        # get koji tasks and populate repo_id
        # format: {build: [koji_task], ...}
        build_tasks = sync_tasks(session, collection, chunk, real=True)
        # discard builds with no repo_id, because those cannot be resolved
        build_tasks = {
            build: tasks for build, tasks in build_tasks.items() if build.repo_id
        }
        if not build_tasks:
            continue
        # get and lock packages to prevent concurrent build insertion
        packages = {
            p.id: p for p in session.db.query(Package)
            .filter(Package.id.in_(build.package_id for build in build_tasks))
            .lock_rows()
        }
        # find builds that may have been inserted in parallel
        # using (package_id, task_id) as lookup key
        # - task_id might not be enough - different collections may use
        #   different koji. same package_id implies same collection
        existing = set(
            session.db.query(Build.package_id, Build.task_id)
            .filter(Build.package_id.in_(packages.keys()))
            .filter(Build.real)
            .filter(Build.task_id.in_(build.task_id for build in build_tasks))
        )
        # discard builds that have already been inserted in parallel
        build_tasks = {
            build: tasks for build, tasks in build_tasks.items()
            if (build.package_id, build.task_id) not in existing
        }
        # log what we're doing
        for build in build_tasks:
            package = packages[build.package_id]
            session.log.info(
                'Registering real build {}-{}-{} for collection {} (task_id {})'
                .format(
                    package.name, build.version, build.release,
                    package.collection,
                    build.task_id,
                )
            )
        # insert valid builds
        session.db.bulk_insert(build_tasks.keys())
        # set build_ids of new koji tasks
        for build, tasks in build_tasks.items():
            for task in tasks:
                task.build_id = build.id
        # insert tasks
        insert_koji_tasks(session, build_tasks)
        # reset priorities
        clear_priority_data(session, packages.values())

        session.db.commit()


def set_failed_build_priority(session, package, last_build):
    """
    Sets packages failed build priority based on the newly registered build.
    """
    failed_priority_value = get_config('priorities.failed_build_priority')
    if last_build.state == Build.FAILED:
        prev_build = session.db.query(Build)\
            .filter(Build.id < last_build.id)\
            .order_by(Build.id.desc())\
            .first()
        if not prev_build or prev_build.state != Build.FAILED:
            package.build_priority = failed_priority_value


def clear_priority_data(session, packages):
    for package in packages:
        package.manual_priority = 0
        package.dependency_priority = 0
        package.build_priority = 0
    session.db.query(UnappliedChange)\
        .filter(UnappliedChange.package_id.in_(p.id for p in packages))\
        .delete()


def update_build_state(session, build, state):
    """
    Updates state of the build in db to new state (Koji state name).
    Cancels builds running too long.
    Deletes canceled builds.
    Sends fedmsg when the build is complete.
    Commits the transaction.
    """
    # pylint: disable=too-many-statements
    try:
        task_timeout = timedelta(0, get_config('koji_config.task_timeout'))
        time_threshold = datetime.now() - task_timeout
        if (state not in Build.KOJI_STATE_MAP and
                (build.started and build.started < time_threshold or
                 build.cancel_requested)):
            session.log.info('Canceling build {0}'.format(build))
            try:
                session.koji('primary').cancelTask(build.task_id)
            except koji.GenericError:
                pass
            state = 'CANCELED'
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            build_id = build.id
            package_id = build.package_id
            session.db.expire_all()
            # lock build
            build = session.db.query(Build).filter_by(id=build_id)\
                .with_lockmode('update').first()
            if not build or build.state == state:
                # other process did the job already
                session.db.rollback()
                return
            if state == Build.CANCELED:
                session.log.info('Deleting build {0} because it was canceled'
                                 .format(build))
                session.db.delete(build)
                session.db.commit()
                return
            assert state in (Build.COMPLETE, Build.FAILED)
            if koji_util.is_koji_fault(session.koji('primary'), build.task_id):
                session.log.info('Deleting build {0} because it ended with Koji fault'
                                 .format(build))
                session.db.delete(build)
                session.db.commit()
                return
            session.log.info('Setting build {build} state to {state}'
                             .format(build=build,
                                     state=Build.REV_STATE_MAP[state]))
            tasks = sync_tasks(session, build.package.collection, [build])
            if build.repo_id is None:
                # Koji problem, no need to bother packagers with this
                session.log.info('Deleting build {0} because it has no repo_id'
                                 .format(build))
                session.db.delete(build)
                session.db.commit()
                return
            insert_koji_tasks(session, tasks)
            session.db.expire(build.package)
            # lock package so there are no concurrent state changes
            # (locking order needs to be build -> package)
            package = session.db.query(Package).filter_by(id=package_id)\
                .with_lockmode('update').one()
            # reset priorities
            clear_priority_data(session, [package])
            # acquire previous state
            # ! this needs to be done *before* updating the build state
            prev_state = package.msg_state_string
            build.state = state
            set_failed_build_priority(session, package, build)
            # refresh package so it haves trigger updated fields
            session.db.flush()
            session.db.expire(package)
            new_state = package.msg_state_string
            # unlock
            session.db.commit()
            if prev_state != new_state:
                dispatch_event(
                    'package_state_change',
                    session=session,
                    package=package,
                    prev_state=prev_state,
                    new_state=new_state,
                )
        else:
            tasks = sync_tasks(session, build.package.collection, [build])
            insert_koji_tasks(session, tasks)
            session.db.commit()
    except (StaleDataError, ObjectDeletedError, IntegrityError):
        # build was deleted concurrently
        session.db.rollback()


def refresh_repo_mappings(session):
    """
    Polls primary koji for createrepo tasks that have secondary counterparts
    and updates their repo mapping in the database
    """
    primary = session.koji('primary')
    for mapping in session.db.query(RepoMapping)\
            .filter_by(primary_id=None):
        task_info = primary.getTaskInfo(mapping.task_id)
        if task_info['state'] in (koji.TASK_STATES['CANCELED'],
                                  koji.TASK_STATES['FAILED']):
            session.db.delete(mapping)
            continue
        for subtask in primary.getTaskChildren(mapping.task_id,
                                               request=True):
            assert subtask['method'] == 'createrepo'
            try:
                mapping.primary_id = subtask['request'][0]
                break
            except KeyError:
                pass


def set_build_repo_id(session, build, task, secondary_mode):
    if build.repo_id:
        return
    try:
        repo_id = task['request'][4]['repo_id']
    except KeyError:
        return
    if repo_id:
        if secondary_mode and not build.real:
            refresh_repo_mappings(session)
            # need to map the repo_id to primary
            mapping = session.db.query(RepoMapping)\
                .filter_by(primary_id=repo_id)\
                .first()
            if mapping:
                build.repo_id = mapping.secondary_id
        else:
            build.repo_id = repo_id


def sync_tasks(session, collection, builds, real=False):
    """
    Synchronizes task and subtask data from Koji.
    Sets properties on build objects passed in and return KojiTask objects.
    Uses koji_session passed as argument.
    Returns map of build to list of tasks
    """
    if not builds:
        return
    koji_session = (session.secondary_koji_for(collection) if real
                    else session.koji('primary'))
    call = itercall(koji_session, builds, lambda k, b: k.getTaskInfo(b.task_id))
    valid_builds = []
    for build, task_info in izip(builds, call):
        if not task_info:
            continue
        build.started = datetime.fromtimestamp(task_info['create_ts'])
        if task_info.get('completion_ts'):
            build.finished = datetime.fromtimestamp(task_info['completion_ts'])
        elif build.state != Build.RUNNING:
            # When fedmsg delivery is fast, the time is not set yet
            build.finished = datetime.now()
        valid_builds.append(build)
    call = itercall(koji_session, valid_builds,
                    lambda k, b: k.getTaskChildren(b.task_id, request=True))
    build_tasks = {}
    for build, subtasks in izip(valid_builds, call):
        tasks = []
        build_arch_tasks = [task for task in subtasks
                            if task['method'] == 'buildArch']
        for task in build_arch_tasks:
            set_build_repo_id(session, build, task, collection.secondary_mode)
            db_task = KojiTask(task_id=task['id'])
            db_task.build_id = build.id
            db_task.state = task['state']
            db_task.arch = task['arch']
            db_task.started = datetime.fromtimestamp(task['create_ts'])
            if task.get('completion_ts'):
                db_task.finished = datetime.fromtimestamp(task['completion_ts'])
            tasks.append(db_task)
        build_tasks[build] = tasks
    return build_tasks


def insert_koji_tasks(session, tasks):
    tasks = [task for build_task in tasks.values() for task in build_task]
    build_ids = [t.build_id for t in tasks]
    if build_ids:
        assert all(build_ids)
        existing_tasks = {t.task_id: t for t in session.db.query(KojiTask)
                          .filter(KojiTask.build_id.in_(build_ids))}
        to_insert = []
        for task in tasks:
            if task.task_id in existing_tasks:
                existing_task = existing_tasks[task.task_id]
                existing_task.state = task.state
                existing_task.started = task.started
                existing_task.finished = task.finished
            else:
                to_insert.append(task)
        session.db.flush()
        session.db.bulk_insert(to_insert)


def refresh_packages(session):
    """
    Refresh packages from Koji: add packages not yet known by Koschei
    and update blocked flag.
    """
    bases = {base.name: base for base
             in session.db.query(BasePackage.id, BasePackage.name)}
    for collection in session.db.query(Collection.id, Collection.dest_tag,
                                       Collection.secondary_mode):
        koji_session = session.secondary_koji_for(collection)
        koji_packages = koji_session.listPackages(tagID=collection.dest_tag,
                                                  inherited=True)
        whitelisted = {p['package_name'] for p in koji_packages if not p['blocked']}
        packages = session.db.query(Package.id, Package.name, Package.blocked)\
            .filter_by(collection_id=collection.id)\
            .all()
        to_update = [p.id for p in packages if p.blocked == (p.name in whitelisted)]
        if to_update:
            session.db.query(Package).filter(Package.id.in_(to_update))\
                .update({'blocked': ~Package.blocked}, synchronize_session=False)
        existing_names = {p.name for p in packages}
        to_add = []
        for pkg_dict in koji_packages:
            name = pkg_dict['package_name']
            if name not in bases.keys():
                base = BasePackage(name=name)
                bases[name] = base
                to_add.append(base)
        session.db.bulk_insert(to_add)
        to_add = []
        for pkg_dict in koji_packages:
            name = pkg_dict['package_name']
            if name not in existing_names:
                pkg = Package(name=name, base_id=bases.get(name).id,
                              collection_id=collection.id, tracked=False,
                              blocked=pkg_dict['blocked'])
                to_add.append(pkg)
        session.db.bulk_insert(to_add)
        session.db.expire_all()


def refresh_latest_builds(session):
    """
    Checks Koji for latest builds of packages and registers possible
    new real builds.
    """
    for collection in session.db.query(Collection):
        koji_session = session.secondary_koji_for(collection)
        infos = koji_session.listTagged(collection.dest_tag, latest=True,
                                        inherit=True)
        existing_task_ids = set(session.db.query(Build.task_id)
                                .join(Build.package)
                                .filter(Package.collection_id == collection.id)
                                .filter(Build.real)
                                .all_flat())
        to_add = [info for info in infos if info['task_id'] not in existing_task_ids]
        if to_add:
            query = session.db.query(Package)\
                .filter(Package.collection_id == collection.id)\
                .filter(Package.name.in_(i['package_name'] for i in to_add))\
                .options(joinedload(Package.last_build))
            if not collection.poll_untracked:
                query = query.filter_by(tracked=True)
            name_mapping = {pkg.name: pkg for pkg in query}
            package_build_infos = []
            for info in to_add:
                package = name_mapping.get(info['package_name'])
                if package and util.is_build_newer(package.last_build, info):
                    package_build_infos.append((package.id, info))
            if package_build_infos:
                register_real_builds(session, collection, package_build_infos)


def sync_tracked(session, tracked, collection_id=None):
    """
    Synchronize package tracked status. End result is that all
    specified packages are present in Koschei and are set to be
    tracked, and all other packages are not tracked.
    """
    packages = session.db.query(Package)
    if collection_id is not None:
        packages = packages.filter_by(collection_id=collection_id)
    packages = packages.all()
    to_update = [p.id for p in packages if p.tracked != (p.name in tracked)]
    if to_update:
        query = session.db.query(Package).filter(Package.id.in_(to_update))
        query.lock_rows()
        query.update({'tracked': ~Package.tracked}, synchronize_session=False)
        session.db.expire_all()
        session.db.flush()
