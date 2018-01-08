# Copyright (C) 2014-2016 Red Hat, Inc.
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

import imp
import os
import logging
import sys

from collections import defaultdict

from koschei.config import get_config

listeners = defaultdict(list)
service_dirs = []


def load_plugins(endpoint, only=None):
    log = logging.getLogger('koschei.plugin')
    plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')
    for name in only if only is not None else get_config('plugins'):
        name = name + '_plugin'
        if name not in sys.modules:
            log.debug('Loading %s plugin', name)
            try:
                descriptor = imp.find_module(name, [plugin_dir])
            except ImportError:
                raise RuntimeError("{} enabled but not installed".format(name))
            imp.load_module(name, *descriptor)
        endpoint_dir = os.path.join(plugin_dir, name)
        qualname = name + '.' + endpoint
        if qualname not in sys.modules:
            try:
                descriptor = imp.find_module(endpoint, [endpoint_dir])
            except ImportError:
                # plugin exists but doesn't have particular endpoint
                continue
            else:
                imp.load_module(qualname, *descriptor)
                service_dir = os.path.join(plugin_dir, name, endpoint, 'services')
                if os.path.isdir(service_dir):
                    service_dirs.append(service_dir)


def listen_event(name):
    def decorator(fn):
        if fn not in listeners[name]:
            listeners[name].append(fn)
        return fn
    return decorator


def dispatch_event(name, *args, **kwargs):
    result = []
    for listener in listeners[name]:
        result.append(listener(*args, **kwargs))
    return result
