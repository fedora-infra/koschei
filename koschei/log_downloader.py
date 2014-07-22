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

import os

from collections import defaultdict

from . import util
from .models import Build, BuildrootDiff
from .service import service_main

log_output_dir = util.config['directories']['build_logs']

@service_main()
def download_logs(db_session, koji_session):
    to_download = db_session.query(Build)\
                   .filter(Build.logs_downloaded == False,
                           Build.state.in_(Build.FINISHED_STATES))\
                   .order_by(Build.id)

    for build in to_download:
        out_dir = os.path.join(log_output_dir, str(build.id))
        for task in koji_session.getTaskChildren(build.task_id):
            if task['method'] == 'buildArch':
                arch_dir = os.path.join(out_dir, task['arch'])
                util.mkdir_if_absent(arch_dir)
                for file_name in koji_session.listTaskOutput(task['id']):
                    if file_name.endswith('.log'):
                        with open(os.path.join(arch_dir, file_name), 'w') as log_file:
                            print('Downloading {} for {}'.format(file_name, build.task_id))
                            log_file.write(koji_session.downloadTaskOutput(task['id'],
                                                                           file_name))
        make_log_diff(db_session, build)
        build.logs_downloaded = True
        db_session.commit()

def installed_pkgs_from_log(root_log):
    with open(root_log) as log:
        pkgs = []
        lines = log.read().split('\n')
        reading = False
        start_delimiters = ('Installed:', 'Dependency Installed:')
        for line in lines:
            if any(line.rstrip().endswith(section) for section
                   in start_delimiters):
                reading = True
            elif 'Child return code was:' in line:
                reading = False
            elif reading:
                pkg_line = line.split()[2:]
                pkgs += [p1 + ' ' + p2 for p1, p2 in zip(pkg_line[::2], pkg_line[1::2])]
        return pkgs

def strip_arch(pkg):
    na, evr = pkg.split(' ')
    name = na.split('.')[0]
    return name + ' ' + evr


def make_log_diff(db_session, build):
    prev = db_session.query(Build).filter_by(package_id=build.package_id)\
                                  .filter(Build.id < build.id)\
                                  .order_by(Build.id.desc()).first()
    if prev:
        pkgs = defaultdict(lambda: [None, None])
        for i, examined in enumerate((prev, build)):
            logdir = os.path.join(log_output_dir, str(examined.id))
            if os.path.isdir(logdir):
                for arch in os.listdir(logdir):
                    root_log = os.path.join(logdir, arch, 'root.log')
                    installed = set(installed_pkgs_from_log(root_log))
                    if arch == 'noarch':
                        installed = {strip_arch(pkg) for pkg in installed}
                    pkgs[arch][i] = installed
        for arch, installed in pkgs.items():
            if installed[0] is not None and installed[1] is not None:
                added = sorted(installed[0].difference(installed[1]))
                removed = sorted(installed[1].difference(installed[0]))
                diff_obj = BuildrootDiff(prev_build_id=prev.id, curr_build_id=build.id,
                                         arch=arch, added=','.join(added),
                                         removed=','.join(removed))
                db_session.add(diff_obj)
                db_session.commit()
