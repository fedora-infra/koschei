import imp
import os

from functools import wraps
from collections import defaultdict

plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')

plugins = []

plugin_hooks = defaultdict(list)

def load_plugins():
    for path in os.listdir(plugin_dir):
        if path.endswith('.py'):
            name = path[:-3]
            descriptor = imp.find_module(name, [plugin_dir])
            imp.load_module(name, *descriptor)

def plugin(*hooks):
    def decorator(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            fn(*args, **kwargs)
        plugins.append(decorated)
        for hook in hooks:
            plugin_hooks[hook].append(decorated)
        return decorated
    return decorator

def call_hooks(hook, *args, **kwargs):
    for fn in plugin_hooks[hook]:
        fn(*args, **kwargs)
