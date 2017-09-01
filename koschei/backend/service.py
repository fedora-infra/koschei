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

import sys
import imp
import logging
import os
import time
import resource

from koschei import util, plugin
from koschei.config import get_config


def load_service(name):
    service_dirs = [os.path.join(os.path.dirname(__file__), 'services')]
    service_dirs += plugin.service_dirs
    if name not in sys.modules:
        try:
            descriptor = imp.find_module(name, service_dirs)
        except ImportError:
            # It may be a plugin
            pass
        else:
            imp.load_module(name, *descriptor)
    return Service.find_service(name)


class Service(object):
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
        memory_limit = self.service_config.get("memory_limit", None)
        if memory_limit:
            current_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if current_memory > memory_limit:
                self.log.info("Memory limit reached: {mem}KB. Exiting"
                              .format(mem=current_memory))
                sys.exit(3)

    def run_service(self):
        interval = self.service_config.get('interval', 3)
        self.log.info("{name} started".format(name=self.get_name()))
        while True:
            self.notify_watchdog()
            try:
                self.main()
            finally:
                self.db.rollback()
                self.db.close()
            self.memory_check()
            self.notify_watchdog()
            time.sleep(interval)

    @classmethod
    def find_service(cls, name):
        if name == cls.get_name():
            return cls
        # pylint: disable=E1101
        for subcls in cls.__subclasses__():
            ret = subcls.find_service(name)
            if ret:
                return ret

    def notify_watchdog(self):
        if get_config('services.{}.watchdog'.format(self.get_name()), None):
            util.sd_notify("WATCHDOG=1")
