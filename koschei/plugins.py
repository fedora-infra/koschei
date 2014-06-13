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

import imp
import os

from collections import defaultdict

from util import log

plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')

plugins = {}
ordered_plugins = []
plugin_classes = []

def load_plugins():
    if plugins:
        return
    for path in os.listdir(plugin_dir):
        if path.endswith('.py'):
            name = path[:-3]
            descriptor = imp.find_module(name, [plugin_dir])
            log.debug('Loading plugins from {}'.format(path))
            imp.load_module(name, *descriptor)
    for cls in plugin_classes:
        plugin = cls()
        plugins[name] = plugin
        key = '{}-{}'.format(plugin.order, name)
        ordered_plugins.append((key, plugin))
    ordered_plugins.sort()

def dispatch_event(event_name, *args, **kwargs):
    ret = []
    for _, plugin in ordered_plugins:
        for method in plugin._event_hooks[event_name]:
            ret.append(method(*args, **kwargs))
    return ret

class _Meta(type):
    def __init__(cls, name, bases, members):
        super(_Meta, cls).__init__(name, bases, members)
        if name != 'Plugin':
            log.info('Loading plugin {}'.format(name))
            plugin_classes.append(cls)

class Plugin(object):
    __metaclass__ = _Meta

    order = 50

    def __init__(self):
        self._event_hooks = defaultdict(list)

    def register_event(self, event_name, method):
        self._event_hooks[event_name].append(method)
