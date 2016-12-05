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

import argparse
import logging
import signal
import sys

from koschei import plugin, backend
from koschei.config import load_config
from koschei.backend import service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('service')
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-s', '--debug-sql', action='store_true')
    args = parser.parse_args()

    load_config(['/usr/share/koschei/config.cfg', '/etc/koschei/config-backend.cfg'])
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.debug_sql:
        logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
    log = logging.getLogger('koschei.main')

    plugin.load_plugins('backend')
    svc = service.load_service(args.service)
    if not svc:
        print("No such service", file=sys.stderr)
        sys.exit(2)
    signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))
    try:
        svc(backend.KoscheiBackendSession()).run_service()
    except Exception:
        log.exception("Service %s crashed.", args.service)
        raise
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == '__main__':
    main()
