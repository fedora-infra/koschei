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

import importlib
import logging
import sys

from collections import defaultdict

from koschei.config import get_config

listeners = defaultdict(list)


def load_plugins(endpoint, only=None):
    log = logging.getLogger('koschei.plugin')
    for name in only if only is not None else get_config('plugins'):
        qualname = f'koschei.plugins.{name}_plugin'
        if qualname not in sys.modules:
            log.debug('Loading %s plugin', name)
            try:
                importlib.import_module(qualname)
            except ImportError:
                raise RuntimeError(f"{name} plugin enabled but not installed")
        qualname = f'{qualname}.{endpoint}'
        if qualname not in sys.modules:
            try:
                importlib.import_module(qualname)
            except ImportError:
                # plugin exists but doesn't have particular endpoint
                continue


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
