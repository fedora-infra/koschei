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

import sys
import imp
import logging
import os
import socket
import time
import resource
import re

from koschei.config import get_config


def load_service(name):
    service_dir = os.path.join(os.path.dirname(__file__), 'services')
    descriptor = imp.find_module(name, [service_dir])
    imp.load_module(name, *descriptor)
    return Service.find_service(name)


def convert_name(name):
    return re.sub(r'([A-Z])', lambda s: '_' + s.group(0).lower(), name)[1:]


class Service(object):
    def __init__(self, session):
        self.session = session
        self.db = session.db
        self.log = session.log = logging.getLogger(
            '{}.{}'.format(type(self).__module__, type(self).__name__),
        )

    def main(self):
        raise NotImplementedError()

    def run_service(self):
        name = convert_name(self.__class__.__name__)
        service_config = get_config('services').get(name, {})
        interval = service_config.get('interval', 3)
        self.log.info("{name} started".format(name=name))
        memory_limit = service_config.get("memory_limit", None)
        while True:
            try:
                self.main()
                if memory_limit:
                    current_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if current_memory > memory_limit:
                        self.log.info("Memory limit reached: {mem}B. Exiting"
                                      .format(mem=current_memory))
                        sys.exit(3)
            finally:
                self.db.close()
            time.sleep(interval)

    @classmethod
    def find_service(cls, name):
        cname = convert_name(cls.__name__)
        if name == cname:
            return cls
        # pylint: disable=E1101
        for subcls in cls.__subclasses__():
            ret = subcls.find_service(name)
            if ret:
                return ret


def sd_notify(msg):
    sock_path = os.environ.get('NOTIFY_SOCKET', None)
    if not sock_path:
        raise RuntimeError("NOTIFY_SOCKET not set")
    if sock_path[0] == '@':
        sock_path = '\0' + sock_path[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg, sock_path)
    finally:
        sock.close()
