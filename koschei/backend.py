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

from datetime import datetime
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import exists

from koschei import util
from koschei.models import (Build, DependencyChange, KojiTask, Package,
                            PackageGroup, PackageGroupRelation, RepoGenerationRequest)
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

    def __init__(self, log, db, koji_session):
        self.log = log
        self.db = db
        self.koji_session = koji_session

    def submit_build(self, package):
        build = Build(package_id=package.id, state=Build.RUNNING)
        name = package.name
        build_opts = {}
        if package.arch_override:
            build_opts = {'arch_override': package.arch_override}
        srpm, srpm_url = (util.get_last_srpm(self.koji_session, name)
                          or (None, None))
        if srpm_url:
            package.manual_priority = 0
            build.task_id = util.koji_scratch_build(self.koji_session, name,
                                                    srpm_url, build_opts)
            build.started = datetime.now()
            build.epoch = srpm['epoch']
            build.version = srpm['version']
            build.release = srpm['release']
            self.db.add(build)
            self.db.flush()
            self.flush_depchanges(build)
            return build

    def flush_depchanges(self, build):
        self.db.query(DependencyChange)\
               .filter_by(package_id=build.package_id)\
               .filter_by(applied_in_id=None)\
               .delete()

    def get_newer_build_if_exists(self, package):
        [info] = self.koji_session.listTagged(util.source_tag,
                                              latest=True,
                                              package=package.name,
                                              inherit=True) or [None]
        if self.is_build_newer(package.last_build, info):
            return info

    def is_build_newer(self, current_build, task_info):
        return (task_info and (not current_build or
                               util.compare_evr((current_build.epoch,
                                                 current_build.version,
                                                 current_build.release),
                                                (task_info['epoch'],
                                                 task_info['version'],
                                                 task_info['release'])) < 0)
                and self.db.query(Build)
                .filter_by(task_id=task_info['task_id'])
                .count() == 0)

    def register_real_build(self, package, build_info):
        # TODO send fedmsg for real builds?
        try:
            state_map = {koji.BUILD_STATES['COMPLETE']: Build.COMPLETE,
                         koji.BUILD_STATES['FAILED']: Build.FAILED}
            build = Build(task_id=build_info['task_id'], real=True,
                          version=build_info['version'], epoch=build_info['epoch'],
                          release=build_info['release'], package_id=package.id,
                          state=state_map[build_info['state']])
            self.db.add(build)
            self.db.flush()
            self._build_completed(build)
            self.flush_depchanges(build)
            self.log.info('Registering real build for {}, task_id {}.'
                          .format(package, build.task_id))
            return build
        except IntegrityError:
            # other daemon adds the same concurrently
            self.db.rollback()

    def update_build_state(self, build, state):
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            build_id = build.id
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
            self.log.info('Setting build {build} state to {state}'
                          .format(build=build,
                                  state=Build.REV_STATE_MAP[state]))
            self._build_completed(build)
            self.db.expire(build.package)
            # lock package so there are no concurrent state changes
            package = self.db.query(Package).filter_by(id=build.package_id)\
                             .with_lockmode('update').one()
            prev_state = package.msg_state_string
            build.state = state
            # unlock
            self.db.commit()
            new_state = package.msg_state_string
            if prev_state != new_state:
                dispatch_event('package_state_change', package=package,
                               prev_state=prev_state,
                               new_state=new_state)

    def _build_completed(self, build):
        task_info = self.koji_session.getTaskInfo(build.task_id)
        if task_info['create_time']:
            build.started = datetime.fromtimestamp(task_info['create_ts'])
        if task_info['completion_time']:
            build.finished = datetime.fromtimestamp(task_info['completion_ts'])
        else:
            # When fedmsg delivery is fast, the time is not set yet
            build.finished = datetime.now()
        subtasks = self.koji_session.getTaskChildren(build.task_id,
                                                     request=True)
        build_arch_tasks = [task for task in subtasks
                            if task['method'] == 'buildArch']
        for task in build_arch_tasks:
            try:
                # They all have the same repo_id, right?
                build.repo_id = task['request'][4]['repo_id']
            except KeyError:
                pass
            started = finished = None
            try:
                started = datetime.fromtimestamp(task['create_ts'])
                finished = datetime.fromtimestamp(task['completion_ts'])
            except (KeyError, TypeError, ValueError):
                pass
            db_task = KojiTask(build_id=build.id,
                               task_id=task['id'],
                               state=task['state'],
                               started=started,
                               finished=finished,
                               arch=task['arch'])
            self.db.add(db_task)
        self.db.flush()

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

    def register_real_builds(self, task_info_mapping):
        """
        Takes a dictionary mapping package (ORM entity) to koji task_infos
        and registers possible real builds.
        """
        for pkg, info in task_info_mapping.items():
            if self.is_build_newer(pkg.last_build, info):
                self.register_real_build(pkg, info)

    def refresh_packages(self):
        """
        Refresh packages from Koji: add packages not yet known by Koschei
        and update blocked flag.
        """
        source_tag = util.koji_config['source_tag']
        koji_packages = self.koji_session.listPackages(tagID=source_tag, inherited=True)
        whitelisted = {p['package_name'] for p in koji_packages if not p['blocked']}
        packages = self.db.query(Package).all()
        to_update = [p.id for p in packages if p.blocked == (p.name in whitelisted)]
        if to_update:
            self.db.query(Package).filter(Package.id.in_(to_update))\
                   .update({'blocked': ~Package.blocked}, synchronize_session=False)
            self.db.expire_all()
            self.db.flush()
        existing_names = {p.name for p in packages}
        to_add = [p for p in koji_packages if p['package_name'] not in existing_names]
        if to_add:
            for p in to_add:
                pkg = Package(name=p['package_name'])
                pkg.blocked = p['blocked']
                pkg.tracked = False
                self.db.add(pkg)
            self.db.flush()

    def refresh_latest_builds(self):
        """
        Checks Koji for latest builds of packages and registers possible
        new real builds.
        """
        packages = self.db.query(Package).options(joinedload(Package.last_build)).all()
        for p in packages:
            self.db.expunge(p)
        source_tag = util.koji_config['source_tag']
        infos = self.koji_session.listTagged(source_tag, latest=True, inherit=True)
        packages = {p.name: p for p in packages}
        self.register_real_builds({packages[i['package_name']]: i for i in infos})

    def add_packages(self, names, group=None, static_priority=None,
                     manual_priority=None):
        packages = self.db.query(Package).filter(Package.name.in_(names))
        nonexistent = set(names) - {p.name for p in packages}
        if nonexistent:
            raise PackagesDontExist(nonexistent)
        newly_added = [p for p in packages if not pkg.tracked]
        for pkg in newly_added:
            pkg.tracked = True
        if group:
            self.add_group(group, packages)
        self.db.flush()
        return newly_added

    def sync_tracked(self, tracked):
        """
        Synchronize package tracked status.  End result is that all
        specified packages are present in Koschei and are set to be
        tracked, and all other packages are not tracked.
        """
        packages = self.db.query(Package).all()
        to_update = [p.id for p in packages if p.tracked != (p.name in tracked)]
        if to_update:
            self.db.query(Package).filter(Package.id.in_(to_update))\
                   .update({'tracked': ~Package.tracked}, synchronize_session=False)
            self.db.expire_all()
            self.db.flush()

    def poll_repo(self):
        curr_repo = util.get_latest_repo(self.koji_session)
        if curr_repo:
            if not self.db.query(exists()
                                 .where(RepoGenerationRequest.repo_id
                                        == curr_repo['id'])).scalar():
                request = RepoGenerationRequest(repo_id=curr_repo['id'])
                self.db.add(request)
                self.db.commit()
