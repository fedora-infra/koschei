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

from koschei import util
from koschei.models import Build, Session

log_output_dir = util.config['directories']['build_logs']

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
                diff_path = os.path.join(logdir, arch, 'root_diff.log')
                diff = ['+ {}'.format(pkg) for pkg in installed[0].difference(installed[1])]
                diff += ['- {}'.format(pkg) for pkg in installed[1].difference(installed[0])]
                diff.sort(key=lambda x: x[2:])
                with open(diff_path, 'w') as diff_file:
                    diff_file.write('\n'.join(diff))

def main():
    import time
    db_session = Session()
    koji_session = util.create_koji_session(anonymous=True)
    print("log_downloader started")
    while True:
        download_logs(db_session, koji_session)
        time.sleep(3)

if __name__ == '__main__':
    main()
