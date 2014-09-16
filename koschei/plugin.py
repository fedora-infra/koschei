import imp
import os
import logging

loaded = False
plugin_dir = os.path.join(os.path.dirname(__file__), 'plugins')
log = logging.getLogger('koschei.plugin')

def load_plugins(only=None):
    global loaded
    if not loaded:
        for path in os.listdir(plugin_dir):
            if path.endswith('.py'):
                name = path[:-3]
                if (only is None or name in only) and not name.startswith('_'):
                    descriptor = imp.find_module(name, [plugin_dir])
                    log.info('Loading {} plugin'.format(name))
                    imp.load_module(name, *descriptor)
        loaded = True
