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

from datetime import datetime, timedelta
from itertools import izip

import koji
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlalchemy.sql import insert

from koschei import util
from koschei.config import get_config
from koschei.backend import koji_util
from koschei.backend.koji_util import itercall
from koschei.models import (Build, UnappliedChange, KojiTask, Package,
                            PackageGroup, PackageGroupRelation,
                            Collection, RepoMapping)
from koschei.plugin import dispatch_event


class PackagesDontExist(Exception):
    def __init__(self, names, *args, **kwargs):
        super(PackagesDontExist, self).__init__(*args, **kwargs)
        self.names = names


def check_package_state(package, prev_state):
    new_state = package.msg_state_string
    if prev_state != new_state:
        dispatch_event('package_state_change', package=package,
                       prev_state=prev_state,
                       new_state=new_state)


class Backend(object):

    def __init__(self, log, db, koji_sessions):
        self.log = log
        self.db = db
        self.koji_sessions = koji_sessions

    def submit_build(self, package):
        build = Build(package_id=package.id, state=Build.RUNNING)
        name = package.name
        build_opts = {}
        if package.arch_override:
            build_opts = {'arch_override': package.arch_override}
        tag = package.collection.target_tag
        # SRPMs are taken from secondary, primary needs to be able to build
        # from relative URL constructed against secondary (internal redirect)
        srpm_res = koji_util.get_last_srpm(self.koji_sessions['secondary'], tag, name)
        if srpm_res:
            srpm, srpm_url = srpm_res
            package.manual_priority = 0
            build.task_id = koji_util.koji_scratch_build(self.koji_sessions['primary'],
                                                         tag, name, srpm_url, build_opts)
            build.started = datetime.now()
            build.epoch = srpm['epoch']
            build.version = srpm['version']
            build.release = srpm['release']
            self.db.add(build)
            self.db.flush()
            self.flush_depchanges(build)
            return build

    def flush_depchanges(self, build):
        self.db.query(UnappliedChange)\
               .filter_by(package_id=build.package_id)\
               .delete(synchronize_session=False)

    def get_newer_build_if_exists(self, package):
        [info] = self.koji_sessions['secondary']\
            .listTagged(package.collection.target_tag, latest=True,
                        package=package.name, inherit=True) or [None]
        if info and self.is_build_newer(package.last_build, info):
            return info

    def is_build_newer(self, current_build, task_info):
        if current_build is None:
            return True
        return util.compare_evr((current_build.epoch,
                                 current_build.version,
                                 current_build.release),
                                (task_info['epoch'],
                                 task_info['version'],
                                 task_info['release'])) < 0

    def register_real_builds(self, package_build_infos):
        """
        Registers real builds for given build infos.
        Takes care of concurrency and commits the transaction.

        :param: package_build_infos tuples in format (package_id, build_info)
        """
        # TODO send fedmsg for real builds?
        state_map = {koji.BUILD_STATES['COMPLETE']: Build.COMPLETE,
                     koji.BUILD_STATES['FAILED']: Build.FAILED}
        builds = [Build(task_id=build_info['task_id'], real=True,
                        version=build_info['version'], epoch=build_info['epoch'],
                        release=build_info['release'], package_id=package_id,
                        state=state_map[build_info['state']])
                  for package_id, build_info in package_build_infos]
        registered = []
        for chunk in util.chunks(builds, 100):  # TODO configurable
            retries = 10
            while True:
                try:
                    build_tasks = self.sync_tasks(chunk,
                                                  self.koji_sessions['secondary'])
                    for build, tasks in build_tasks.items():
                        if not build.repo_id:
                            del build_tasks[build]
                    chunk = build_tasks.keys()
                    self.db.bulk_insert(chunk)
                    for build, tasks in build_tasks.items():
                        for task in tasks:
                            task.build_id = build.id
                    self.insert_koji_tasks(build_tasks)
                    self.db.commit()
                    registered += chunk
                    break
                except IntegrityError:
                    retries -= 1
                    if not retries:
                        raise
                    self.db.rollback()
                    self.log.info("Retrying real build insertion")
                    existing_ids = self.db.query(Build.task_id)\
                        .filter_by(real=True)\
                        .filter(Build.task_id.in_(b.task_id for b in chunk))\
                        .all()
                    existing_ids = {b.task_id for [b] in existing_ids}
                    chunk = [b for b in chunk if b.task_id not in existing_ids]

        if registered:
            # pylint:disable=unused-variable
            # used via sqla cache
            pkgs = self.db.query(Package)\
                .filter(Package.id.in_(b.package_id for b in registered))\
                .all()
            for build in registered:
                package = self.db.query(Package).get(build.package_id)
                self.log.info(
                    'Registering real build {}-{}-{} for collection {} (task_id {})'
                    .format(package.name, build.version, build.release,
                            self.db.query(Collection).get(package.collection_id),
                            build.task_id))

    def update_build_state(self, build, state):
        """
        Updates state of the build in db to new state (Koji state name).
        Cancels builds running too long.
        Deletes canceled builds.
        Sends fedmsg when the build is complete.
        Commits the transaction.
        """
        try:
            task_timeout = timedelta(0, get_config('koji_config.task_timeout'))
            time_threshold = datetime.now() - task_timeout
            if (state not in Build.KOJI_STATE_MAP and
                    (build.started and build.started < time_threshold or
                     build.cancel_requested)):
                self.log.info('Canceling build {0}'.format(build))
                try:
                    self.koji_sessions['primary'].cancelTask(build.task_id)
                except koji.GenericError:
                    pass
                state = 'CANCELED'
            if state in Build.KOJI_STATE_MAP:
                state = Build.KOJI_STATE_MAP[state]
                build_id = build.id
                package_id = build.package_id
                self.db.expire_all()
                # lock build
                build = self.db.query(Build).filter_by(id=build_id)\
                               .with_lockmode('update').first()
                if not build or build.state == state:
                    # other process did the job already
                    self.db.rollback()
                    return
                if state == Build.CANCELED:
                    self.log.info('Deleting build {0} because it was canceled'
                                  .format(build))
                    self.db.delete(build)
                    self.db.commit()
                    return
                assert state in (Build.COMPLETE, Build.FAILED)
                if koji_util.is_koji_fault(self.koji_sessions['primary'], build.task_id):
                    self.log.info('Deleting build {0} because it ended with Koji fault'
                                  .format(build))
                    self.db.delete(build)
                    self.db.commit()
                    return
                self.log.info('Setting build {build} state to {state}'
                              .format(build=build,
                                      state=Build.REV_STATE_MAP[state]))
                tasks = self.sync_tasks([build], self.koji_sessions['primary'])
                if build.repo_id is None:
                    # Koji problem, no need to bother packagers with this
                    self.log.info('Deleting build {0} because it has no repo_id'
                                  .format(build))
                    self.db.delete(build)
                    self.db.commit()
                    return
                self.insert_koji_tasks(tasks)
                self.db.expire(build.package)
                # lock package so there are no concurrent state changes
                package = self.db.query(Package).filter_by(id=package_id)\
                                 .with_lockmode('update').one()
                prev_state = package.msg_state_string
                build.state = state
                self.db.flush()
                self.db.expire(package)
                new_state = package.msg_state_string
                # unlock
                self.db.commit()
                if prev_state != new_state:
                    dispatch_event('package_state_change', package=package,
                                   prev_state=prev_state,
                                   new_state=new_state)
            else:
                tasks = self.sync_tasks([build], self.koji_sessions['primary'])
                self.insert_koji_tasks(tasks)
                self.db.commit()
        except (StaleDataError, ObjectDeletedError, IntegrityError):
            # build was deleted concurrently
            self.db.rollback()

    def refresh_repo_mappings(self):
        primary = self.koji_sessions['primary']
        for mapping in self.db.query(RepoMapping)\
                .filter_by(primary_id=None):
            task_info = primary.getTaskInfo(mapping.task_id)
            if task_info['state'] in (koji.TASK_STATES['CANCELED'],
                                      koji.TASK_STATES['FAILED']):
                self.db.delete(mapping)
                continue
            for subtask in primary.getTaskChildren(mapping.task_id,
                                                   request=True):
                assert subtask['method'] == 'createrepo'
                try:
                    mapping.primary_id = subtask['request'][0]
                    break
                except KeyError:
                    pass

    def set_build_repo_id(self, build, task):
        if build.repo_id:
            return
        try:
            repo_id = task['request'][4]['repo_id']
        except KeyError:
            return
        if repo_id:
            if get_config('secondary_mode') and not build.real:
                self.refresh_repo_mappings()
                # need to map the repo_id to primary
                mapping = self.db.query(RepoMapping)\
                    .filter_by(primary_id=repo_id)\
                    .first()
                if mapping:
                    build.repo_id = mapping.secondary_id
            else:
                build.repo_id = repo_id

    def sync_tasks(self, builds, koji_session):
        """
        Synchronizes task and subtask data from Koji.
        Sets properties on build objects passed in and return KojiTask objects.
        Uses koji_session passed as argument.
        Returns map of build to list of tasks
        """
        call = itercall(koji_session, builds, lambda k, b: k.getTaskInfo(b.task_id))
        for build, task_info in izip(builds, call):
            if task_info.get('create_ts'):
                build.started = datetime.fromtimestamp(task_info['create_ts'])
            if task_info.get('completion_ts'):
                build.finished = datetime.fromtimestamp(task_info['completion_ts'])
            elif build.state != Build.RUNNING:
                # When fedmsg delivery is fast, the time is not set yet
                build.finished = datetime.now()
        call = itercall(koji_session, builds,
                        lambda k, b: k.getTaskChildren(b.task_id, request=True))
        build_tasks = {}
        for build, subtasks in izip(builds, call):
            tasks = []
            build_arch_tasks = [task for task in subtasks
                                if task['method'] == 'buildArch']
            for task in build_arch_tasks:
                self.set_build_repo_id(build, task)
                db_task = KojiTask(task_id=task['id'])
                db_task.build_id = build.id
                db_task.state = task['state']
                db_task.arch = task['arch']
                if task.get('create_ts'):
                    db_task.started = datetime.fromtimestamp(task['create_ts'])
                if task.get('completion_ts'):
                    db_task.finished = datetime.fromtimestamp(task['completion_ts'])
                tasks.append(db_task)
            build_tasks[build] = tasks
        return build_tasks

    def insert_koji_tasks(self, tasks):
        tasks = [task for build_task in tasks.values() for task in build_task]
        build_ids = [t.build_id for t in tasks]
        if build_ids:
            assert all(build_ids)
            existing_tasks = {t.task_id: t for t in self.db.query(KojiTask)
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
            self.db.flush()
            self.db.bulk_insert(to_insert)

    def add_group(self, group, pkgs):
        group_obj = self.db.query(PackageGroup)\
                           .filter_by(name=group).first()
        if not group_obj:
            group_obj = PackageGroup(name=group)
            self.db.add(group_obj)
            self.db.flush()
        for pkg in pkgs:
            rel = PackageGroupRelation(group_id=group_obj.id,
                                       package_id=pkg.id)
            self.db.add(rel)

    def set_group_content(self, group, contents):
        rels = []
        for name in contents:
            rels.append(dict(group_id=group.id, package_name=name))
        self.db.query(PackageGroupRelation).filter_by(group_id=group.id).delete()
        self.db.execute(insert(PackageGroupRelation, rels))

    def refresh_packages(self):
        """
        Refresh packages from Koji: add packages not yet known by Koschei
        and update blocked flag.
        """
        for collection in self.db.query(Collection):
            koji_packages = self.koji_sessions['secondary']\
                .listPackages(tagID=collection.target_tag, inherited=True)
            whitelisted = {p['package_name'] for p in koji_packages if not p['blocked']}
            packages = self.db.query(Package).filter_by(collection_id=collection.id).all()
            to_update = [p.id for p in packages if p.blocked == (p.name in whitelisted)]
            if to_update:
                self.db.query(Package).filter(Package.id.in_(to_update))\
                       .update({'blocked': ~Package.blocked}, synchronize_session=False)
                self.db.flush()
            existing_names = {p.name for p in packages}
            to_add = [p for p in koji_packages if p['package_name'] not in existing_names]
            if to_add:
                for p in to_add:
                    pkg = Package(name=p['package_name'], collection_id=collection.id)
                    pkg.blocked = p['blocked']
                    pkg.tracked = False
                    self.db.add(pkg)
                self.db.flush()
            self.db.expire_all()

    def refresh_latest_builds(self):
        """
        Checks Koji for latest builds of packages and registers possible
        new real builds.
        """
        for collection in self.db.query(Collection):
            tag = collection.target_tag
            infos = self.koji_sessions['secondary']\
                .listTagged(tag, latest=True, inherit=True)
            existing_task_ids = set(self.db.query(Build.task_id)
                                    .join(Build.package)
                                    .filter(Package.collection_id == collection.id)
                                    .filter(Build.real)
                                    .all_flat())
            to_add = [info for info in infos if info['task_id'] not in existing_task_ids]
            if to_add:
                query = self.db.query(Package)\
                    .filter(Package.collection_id == collection.id)\
                    .filter(Package.name.in_(i['package_name'] for i in to_add))\
                    .options(joinedload(Package.last_build))
                if not collection.poll_untracked:
                    query = query.filter_by(tracked=True)
                name_mapping = {pkg.name: pkg for pkg in query}
                package_build_infos = []
                for info in to_add:
                    package = name_mapping.get(info['package_name'])
                    if package and self.is_build_newer(package.last_build, info):
                        package_build_infos.append(
                            (name_mapping[info['package_name']].id, info)
                        )
                if package_build_infos:
                    self.register_real_builds(package_build_infos)

    def add_packages(self, names, collection_id=None):
        query = self.db.query(Package).filter(Package.name.in_(names))
        if collection_id:
            query = query.filter_by(collection_id=collection_id)
        packages = query.all()
        if len(packages) != len(names):
            nonexistent = set(names) - {p.name for p in packages}
            raise PackagesDontExist(names=nonexistent)
        query = self.db.query(Package).filter(Package.name.in_(names))
        if collection_id:
            query = query.filter_by(collection_id=collection_id)
        query.update({'tracked': True})

    def sync_tracked(self, tracked, collection_id=None):
        """
        Synchronize package tracked status. End result is that all
        specified packages are present in Koschei and are set to be
        tracked, and all other packages are not tracked.
        """
        packages = self.db.query(Package)
        if collection_id is not None:
            packages = packages.filter_by(collection_id=collection_id)
        packages = packages.all()
        to_update = [p.id for p in packages if p.tracked != (p.name in tracked)]
        if to_update:
            query = self.db.query(Package).filter(Package.id.in_(to_update))
            query.lock_rows()
            query.update({'tracked': ~Package.tracked}, synchronize_session=False)
            self.db.expire_all()
            self.db.flush()
