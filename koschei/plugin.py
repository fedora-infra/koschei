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

from collections import defaultdict

from koschei.config import get_config

loaded = {}

listeners = defaultdict(list)
service_dirs = []


def load_plugins(endpoint, only=None):
    log = logging.getLogger('koschei.plugin')
    if endpoint not in loaded:
        loaded[endpoint] = {}
        plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')
        for name in only if only is not None else get_config('plugins'):
            log.info('Loading %s plugin', name)
            try:
                descriptor = imp.find_module(name, [plugin_dir])
            except ImportError:
                raise RuntimeError("{} plugin enabled but not installed".format(name))
            plugin = imp.load_module(name, *descriptor)
            try:
                descriptor = imp.find_module(endpoint, plugin.__path__)
            except ImportError:
                # plugin exists but doesn't have particular endpoint
                pass
            else:
                loaded[endpoint][name] = imp.load_module(
                    name + '.' + endpoint,
                    *descriptor
                )
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
