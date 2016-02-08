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

import logging
import signal
import sys
import time

from . import util
from .models import Session


class Service(object):

    def __init__(self, log=None, db=None):
        signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))
        self.log = log or logging.getLogger(
            'koschei.' + self.__class__.__name__.lower())
        self.db = db or Session()

    def main(self):
        raise NotImplementedError()

    def run_service(self):
        name = self.__class__.__name__.lower()
        service_config = util.config.get('services', {})\
                                    .get(name, {})
        interval = service_config.get('interval', 3)
        self.log.info("{name} started".format(name=name))
        while True:
            try:
                self.main()
                time.sleep(interval)
            except KeyboardInterrupt:
                sys.exit(0)
            finally:
                self.db.rollback()

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
        self.koji_sessions = koji_sessions or {
            'primary': util.KojiSession(anonymous=self.koji_anonymous),
            'secondary': util.KojiSession(koji_config=util.secondary_koji_config)
        }
