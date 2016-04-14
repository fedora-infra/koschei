import imp
import os
import logging

from collections import defaultdict

from koschei.config import get_config

loaded = {}
log = logging.getLogger('koschei.plugin')

listeners = defaultdict(list)


def load_plugins(endpoint, only=None):
    if endpoint not in loaded:
        loaded[endpoint] = {}
        plugin_dir = os.path.join(os.path.dirname(__file__), endpoint, 'plugins')
        for name in only if only is not None else get_config('plugins'):
            descriptor = imp.find_module(name, [plugin_dir])
            log.info('Loading %s plugin', name)
            loaded[endpoint][name] = imp.load_module(name, *descriptor)


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
