import imp
import os

from collections import defaultdict

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
            print 'Loading {}'.format(name)
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
            plugin_classes.append(cls)

class Plugin(object):
    __metaclass__ = _Meta

    order = 50

    def __init__(self):
        self._event_hooks = defaultdict(list)

    def register_event(self, event_name, method):
        self._event_hooks[event_name].append(method)
