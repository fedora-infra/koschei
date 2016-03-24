import imp
import os
import logging

from collections import defaultdict

from koschei import util

loaded = {}
plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')
log = logging.getLogger('koschei.plugin')

listeners = defaultdict(list)


def load_plugins(only=None):
    if not loaded:
        for name in only if only is not None else util.config['plugins']:
            descriptor = imp.find_module(name, [plugin_dir])
            log.info('Loading %s plugin', name)
            loaded[name] = imp.load_module(name, *descriptor)


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
