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

"""
This module provides base class for all backend service.
"""

import sys
import importlib
import logging
import os
import re
import time

from koschei import util
from koschei.config import get_config


def load_service(name):
    """
    Used to import and create the service instance for given name.
    Looks into service modules of backend and all plugins.
    """
    for module in list(sys.modules):
        if re.match(r'^koschei\.(?:.+\.)?backend', module):
            try:
                importlib.import_module(f'{module}.services.{name}')
            except ImportError:
                # given module doesn't provide given service submodule
                continue
    return Service.find_service(name)


class Service(object):
    """
    Base class of all backend services. Contains the session. Takes care of running the
    main method in a loop while doing memory checks and watchdog invocations.
    """
    def __init__(self, session):
        self.session = session
        self.db = session.db
        self.log = session.log = logging.getLogger(
            '{}.{}'.format(type(self).__module__, type(self).__name__),
        )
        self.service_config = get_config('services').get(self.get_name(), {})

    @classmethod
    def get_name(cls):
        return util.to_snake_case(cls.__name__)

    def main(self):
        raise NotImplementedError()

    def memory_check(self):
        """
        Check whether the process exceeds memory limits specified in configuration
        (by default there is no limit). If it does, the process exits with code 3.
        """
        resident_limit = self.service_config.get("memory_limit", None)
        virtual_limit = self.service_config.get("virtual_memory_limit", None)
        if resident_limit or virtual_limit:
            # see man 5 proc, search for statm
            with open('/proc/self/statm') as statm_f:
                statm = statm_f.readline().split()
            page_size = os.sysconf("SC_PAGE_SIZE") / 1024
            virtual, resident = [int(pages) * page_size for pages in statm[0:2]]
            if (
                    (resident_limit and resident > resident_limit) or
                    (virtual_limit and virtual > virtual_limit)
            ):
                self.log.info("Memory limit reached - resident: {resident} KiB, "
                              "virtual: {virtual} KiB. Exiting."
                              .format(virtual=virtual, resident=resident))
                sys.exit(3)

    def run_service(self):
        """
        Run service's main method in a loop with sleep in between.
        """
        interval = self.service_config.get('interval', 3)
        self.log.info("{name} started".format(name=self.get_name()))
        while True:
            self.notify_watchdog()
            try:
                self.main()
            finally:
                self.db.rollback()
            self.memory_check()
            self.notify_watchdog()
            time.sleep(interval)

    @classmethod
    def find_service(cls, name):
        """
        Find service class by name.
        """
        if name == cls.get_name():
            return cls
        # pylint: disable=E1101
        for subcls in cls.__subclasses__():
            ret = subcls.find_service(name)
            if ret:
                return ret

    def notify_watchdog(self):
        """
        Notify watchdog (if enabled) that the process is not stuck.
        """
        if get_config('services.{}.watchdog'.format(self.get_name()), None):
            path = os.environ.get('WATCHDOG_PATH', None)
            if not path:
                raise RuntimeError("WATCHDOG_PATH not set")
            with open(path, 'w'):
                pass
