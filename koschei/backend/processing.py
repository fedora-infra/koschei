# Copyright (C) 2017 Red Hat, Inc.
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
import koji

from koschei.config import get_config
from koschei.backend import koji_util


class BuildProcessor(object):
    """
    Base class for build processors. Build processor is a class that performs
    additional processing of a build after it has finished, such as log
    analysis.
    """

    # A list of processor names that need to be run before this one can proceed
    requires = []

    def __init__(self, session):
        self.session = session

    def process_build(self, build):
        """
        Processes a single build
        """
        raise NotImplementedError()

    def cleanup(self, build):
        """
        Performs cleanup before a build is deleted. Should delete processing
        results store outside database if any.
        """
        pass


def run_build_processors(session, build):
    """
    Runs all build processors for given build
    """
    processors = BuildProcessor.__subclasses__()
    # TODO order
    for processor in processors:
        processor(session).process_build(build)


class LogDownloader(BuildProcessor):
    def process_build(self, build):
        log_names = {'build.log', 'root.log'}
        out_dir = os.path.join(get_config('directories.build_logs'), str(build.id))
        koji_session = self.session.koji_for_build(build)
        for task in build.build_arch_tasks:
            if not log_names.intersection(koji_session.listTaskOutput(task.task_id)):
                return
            arch_dir = os.path.join(out_dir, task.arch)
            if not os.path.isdir(arch_dir):
                os.makedirs(arch_dir)
            for file_name in log_names:
                file_path = os.path.join(arch_dir, file_name)
                if not os.path.isfile(file_path):
                    self.session.log.debug(
                        'Downloading {} for {}'.format(file_name, build.task_id)
                    )
                    try:
                        koji_util.download_task_output(
                            koji_session=koji_session,
                            task_id=task.task_id,
                            file_name=file_name,
                            out_path=file_path,
                        )
                    except koji.GenericError:
                        self.session.log.info(
                            'Cannot download {} for {}'.format(file_name, build.task_id)
                        )
                        # TODO skip dependent
