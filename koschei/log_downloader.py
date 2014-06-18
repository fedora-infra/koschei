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

from koschei import util
from koschei.models import Build, Session

log_output_dir = util.config['directories']['build_logs']

def download_logs(db_session, koji_session):
    to_download = db_session.query(Build)\
                   .filter(Build.logs_downloaded == False,
                           Build.state.in_(Build.FINISHED_STATES)).all()

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
        build.logs_downloaded = True
        db_session.commit()

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
