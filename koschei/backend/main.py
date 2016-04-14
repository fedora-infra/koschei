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
from __future__ import print_function

import logging
import signal
import sys

from koschei import plugin
from koschei.config import load_config
from koschei.backend import service


# TODO: move this to plugins requiring fedmsg
def init_fedmsg():
    try:
        import fedmsg
        import fedmsg.meta
        fedmsg_config = fedmsg.config.load_config()
        fedmsg.meta.make_processors(**fedmsg_config)
    except ImportError:
        print("Unable to initialize fedmsg", file=sys.stderr)


if __name__ == '__main__':
    load_config(['/usr/share/koschei/config.cfg', '/etc/koschei/config-backend.cfg'])
    log = logging.getLogger('koschei.main')

    if len(sys.argv) < 2:
        print("Requires service name", file=sys.stderr)
        sys.exit(2)
    name = sys.argv[1]
    plugin.load_plugins('backend')
    init_fedmsg()
    service = service.load_service(name)
    if not service:
        print("No such service", file=sys.stderr)
        sys.exit(2)
    signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))
    try:
        service().run_service()
    except Exception:
        log.exception("Service %s crashed.", name)
        raise
    except KeyboardInterrupt:
        sys.exit(0)
