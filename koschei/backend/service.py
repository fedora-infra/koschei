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

from koschei.config import get_config
from koschei.models import Session
from koschei.backend.koji_util import KojiSession


def load_service(name):
    service_dir = os.path.join(os.path.dirname(__file__), 'services')
    descriptor = imp.find_module(name, [service_dir])
    imp.load_module(name, *descriptor)
    return Service.find_service(name)


class Service(object):

    def __init__(self, log=None, db=None):
        self.log = log or logging.getLogger(
            'koschei.' + self.__class__.__name__.lower())
        self.db = db or Session()

    def main(self):
        raise NotImplementedError()

    def run_service(self):
        name = self.__class__.__name__.lower()
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
        if name == cls.__name__.lower():
            return cls
        # pylint: disable=E1101
        for subcls in cls.__subclasses__():
            ret = subcls.find_service(name)
            if ret:
                return ret


class KojiService(Service):
    koji_anonymous = True

    def __init__(self, koji_sessions=None, **kwargs):
        super(KojiService, self).__init__(**kwargs)
        if koji_sessions:
            self.koji_sessions = koji_sessions
        else:
            primary_koji = KojiSession(anonymous=self.koji_anonymous)
            secondary_koji = primary_koji
            if get_config('secondary_koji_config'):
                secondary_koji = KojiSession(koji_id='secondary')

            self.koji_sessions = {
                'primary': primary_koji,
                'secondary': secondary_koji
            }

    def secondary_session_for(self, collection):
        """
        Returns secondary session for secondary mode and primary otherwise.

        :param: collection collection object
        """
        return self.koji_sessions['secondary' if collection.secondary_mode
                                  else 'primary']


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
