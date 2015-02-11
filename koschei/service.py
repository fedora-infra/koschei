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

import koji
import logging
import requests
import signal
import sys
import socket
import time
import fedmsg.core
import fedmsg.config

from . import util
from .models import Session


class Service(object):
    retry_on = ()

    def __init__(self, log=None, db=None):
        signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))
        self.log = log or logging.getLogger(
            'koschei.' + self.__class__.__name__.lower())
        self.db = db or Session()

    def main(self):
        raise NotImplementedError()

    def get_handled_exceptions(self):
        return list()

    def on_exception(self, exc):
        pass

    def run_service(self):
        name = self.__class__.__name__.lower()
        service_config = util.config.get('services', {})\
                                    .get(name, {})
        interval = service_config.get('interval', 3)
        retry_in = service_config.get('base_retry_interval', 10)
        retry_attempts = 0
        self.log.info("{name} started".format(name=name))
        handled_exceptions = tuple(self.get_handled_exceptions())
        while True:
            try:
                self.main()
                retry_attempts = 0
                time.sleep(interval)
            except KeyboardInterrupt:
                sys.exit(0)
            except handled_exceptions as exc:
                while True:
                    try:
                        retry_attempts += 1
                        self.log.error("Service error: {}".format(exc))
                        sleep = retry_in * retry_attempts
                        self.log.info("Retrying in {} seconds".format(sleep))
                        time.sleep(sleep)
                        self.on_exception(exc)
                        break
                    except handled_exceptions as exc:
                        pass
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
    __retry_on = (koji.GenericError, socket.error)

    def __init__(self, koji_session=None, **kwargs):
        super(KojiService, self).__init__(**kwargs)
        self.koji_session = util.Proxy(koji_session
                                       or self.create_koji_session())

    @classmethod
    def create_koji_session(cls):
        return util.create_koji_session(anonymous=cls.koji_anonymous)

    def get_handled_exceptions(self):
        return (list(self.__retry_on) +
                super(KojiService, self).get_handled_exceptions())

    def on_exception(self, exc):
        self.koji_session.proxied = self.create_koji_session()
        super(KojiService, self).on_exception(exc)

class FedmsgService(Service):
    def __init__(self, fedmsg_context=None, fedmsg_config=None, **kwargs):
        self.fedsmg_config = (fedmsg_config or
                              fedmsg.config.load_config([], None))
        self.fedmsg = (fedmsg_context or
                       fedmsg.core.FedMsgContext(**self.fedsmg_config))
        super(FedmsgService, self).__init__(**kwargs)

    def get_handled_exceptions(self):
        return ([requests.exceptions.ConnectionError] +
                super(FedmsgService, self).get_handled_exceptions())

    def on_exception(self, exc):
        self.fedmsg.destroy()
        self.fedmsg = fedmsg.core.FedMsgContext(**self.fedsmg_config)
        super(FedmsgService, self).on_exception(exc)
