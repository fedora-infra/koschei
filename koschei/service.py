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

def service_main(function, needs_koji=True, koji_anonymous=True):
    key = function.__module__
    log = logging.getLogger(key)
    service_config = util.config.get('services', {}).get(key, {})
    interval = service_config.get('interval', 3)

    signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))

    args = {'db_session': Session()}

    while True:
        try:
            if needs_koji and 'koji_session' not in args:
                args['koji_session'] = util.create_koji_session(anonymous=koji_anonymous)
            function(**args)
            args['db_session'].expire_all()
            time.sleep(interval)
        except KeyboardInterrupt:
            sys.exit(0)
