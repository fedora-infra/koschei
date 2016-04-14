# Copyright (C) 2016  Red Hat, Inc.
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

import os
import sys
import logging
import logging.config

__config = {}


def merge_dict(d1, d2):
    ret = d1.copy()
    for k, v in d2.items():
        if k in ret and isinstance(v, dict) and isinstance(ret[k], dict):
            ret[k] = merge_dict(ret[k], v)
        else:
            ret[k] = v
    return ret


def load_config(config_paths):
    def parse_config(config_path):
        if os.path.exists(config_path):
            with open(config_path) as config_file:
                code = compile(config_file.read(), config_path, 'exec')
                conf_locals = {}
                exec code in conf_locals
                if 'config' in conf_locals:
                    return conf_locals['config']
        else:
            raise RuntimeError("Config file not found: {}".format(config_path))
    config = {}
    for config_path in config_paths:
        print("Loading config from {}".format(config_path), file=sys.stderr)
        config = merge_dict(config, parse_config(config_path))

    assert config

    config['secondary_mode'] = bool(config.get('secondary_koji_config', False))
    secondary_koji_config = dict(config['koji_config'])
    secondary_koji_config.update(config.get('secondary_koji_config', {}))
    config['secondary_koji_config'] = secondary_koji_config

    logging.config.dictConfig(config['logging'])

    global __config
    __config = config


NO_DEFAULT = object()


def get_config(key, default=NO_DEFAULT):
    if not __config:
        raise RuntimeError("No configuration loaded")
    ret = __config
    if key is None:
        return ret
    try:
        for component in key.split('.'):
            ret = ret[component]
    except KeyError:
        if default is not NO_DEFAULT:
            return default
        else:
            raise KeyError("Configuration value not found: {}".format(key))
    return ret


def get_koji_config(koji_id, key):
    if koji_id == 'primary':
        return get_config('koji_config.{}'.format(key))
    elif koji_id == 'secondary':
        return get_config('secondary_koji_config.{}'.format(key))
    raise RuntimeError("Koji ID not found: {}".format(koji_id))
