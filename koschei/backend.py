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

import json

from datetime import datetime

from . import util
from .models import Build, DependencyChange, KojiTask, Session, Package,\
                    PackageGroup, PackageGroupRelation
from .event import Event

class PackagesDontExist(Exception):
    def __init__(self, names, *args, **kwargs):
        super(PackagesDontExist, self).__init__(*args, **kwargs)
        self.names = names

class PackageStateUpdateEvent(Event):
    def __init__(self, package, prev_state, new_state):
        self.package = package
        self.prev_state = prev_state
        self.new_state = new_state

def check_package_state(package, prev_pkg_state):
    db_session = Session.object_session(package)
    new_pkg_state = package.state_string
    if prev_pkg_state != new_pkg_state:
        event = PackageStateUpdateEvent(package, prev_pkg_state, new_pkg_state)
        db_session._event_queue.add(event)

class Backend(object):
    def __init__(self, log, db_session, koji_session):
        self.log = log
        self.db_session = db_session
        self.koji_session = koji_session

    def submit_build(self, package):
        build = Build(package_id=package.id, state=Build.RUNNING)
        name = package.name
        build.state = Build.RUNNING
        build_opts = None
        if package.build_opts:
            build_opts = json.loads(package.build_opts)
        srpm, srpm_url = util.get_last_srpm(self.koji_session, name) or (None, None)
        if srpm_url:
            package.manual_priority = 0
            build.task_id = util.koji_scratch_build(self.koji_session, name,
                                                    srpm_url, build_opts)
            build.started = datetime.now()
            build.epoch = srpm['epoch']
            build.version = srpm['version']
            build.release = srpm['release']
            self.db_session.add(build)
            self.db_session.flush()
            self.db_session.query(DependencyChange).filter_by(package_id=build.package_id)\
                                                   .filter_by(applied_in_id=None)\
                                                   .update({'applied_in_id': build.id})
        else:
            package.ignored = True

    def update_build_state(self, build, state):
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            if state == Build.CANCELED:
                self.log.info('Deleting build {0} because it was canceled'\
                              .format(build))
                self.db_session.delete(build)
            else:
                self.log.info('Setting build {build} state to {state}'\
                              .format(build=build, state=Build.REV_STATE_MAP[state]))
                prev_pkg_state = build.package.state_string
                build.state = state
                if state in (Build.COMPLETE, Build.FAILED):
                    self.build_completed(build)
                    check_package_state(build.package, prev_pkg_state)
            self.db_session.commit()

    def build_completed(self, build):
        task_info = self.koji_session.getTaskInfo(build.task_id)
        if task_info['completion_time']:
            build.finished = util.parse_koji_time(task_info['completion_time'])
        else:
            # When fedmsg delivery is fast, the time is not set yet
            build.finished = datetime.now()
        subtasks = self.koji_session.getTaskChildren(build.task_id, request=True)
        build_arch_tasks = [task for task in subtasks if task['method'] == 'buildArch']
        with util.skip_on_integrity_violation(self.db_session):
            for task in build_arch_tasks:
                try:
                    # They all have the same repo_id, right?
                    build.repo_id = task['request'][4]['repo_id']
                except KeyError:
                    pass
                db_task = KojiTask(build_id=build.id, task_id=task['id'],
                                   state=task['state'], started=task['create_time'],
                                   finished=task['completion_time'], arch=task['arch'])
                self.db_session.add(db_task)

    def add_group(self, group, pkgs):
        group_obj = self.db_session.query(PackageGroup).filter_by(name=group).first()
        if not group_obj:
            group_obj = PackageGroup(name=group)
            self.db_session.add(group_obj)
            self.db_session.flush()
        for pkg in pkgs:
            rel = PackageGroupRelation(group_id=group_obj.id, package_id=pkg.id)
            self.db_session.add(rel)

    def add_packages(self, names, group=None, static_priority=None,
                     manual_priority=None):
        newly_added_prio = util.config['priorities']['newly_added']
        existing = [x for [x] in self.db_session.query(Package.name)
                                                .filter(Package.name.in_(names))]
        koji_pkgs = util.get_koji_packages(names)
        nonexistent = [name for name, pkg in zip(names, koji_pkgs) if not pkg]
        if nonexistent:
            raise PackagesDontExist(nonexistent)
        pkgs = []
        for name in names:
            if name not in existing:
                pkg = Package(name=name)
                pkg.static_priority = static_priority or 0
                pkg.manual_priority = manual_priority or newly_added_prio
                self.db_session.add(pkg)
                pkgs.append(pkg)
        self.db_session.flush()
        if group:
            self.add_group(group, pkgs)
        return pkgs
