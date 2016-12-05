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

from __future__ import print_function, absolute_import

import os
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


def parse_config(config_path):
    if os.path.exists(config_path):
        with open(config_path) as config_file:
            code = compile(config_file.read(), config_path, 'exec')
            conf_vars = {}
            exec(code, conf_vars)
            if 'config' in conf_vars:
                return conf_vars['config']
    else:
        raise RuntimeError("Config file not found: {}".format(config_path))


def load_config(config_paths, ignore_env=False):
    """
    Loads configuration from given files and merges the recursively into one
    dictionary, that is then accesible using get_config function. This function
    should be called exacly once during application lifetime.

    :param: config_paths
            list of paths to config files. They must exist.
    :param: ignore_env
            whether to enable overriding the paths by environment
            variable
    """
    config = {}
    if not ignore_env and 'KOSCHEI_CONFIG' in os.environ:
        config_paths = os.environ['KOSCHEI_CONFIG'].split(':')
    for config_path in config_paths:
        config = merge_dict(config, parse_config(config_path))

    assert config

    secondary_koji_config = dict(config['koji_config'])
    secondary_koji_config.update(config.get('secondary_koji_config', {}))
    config['secondary_koji_config'] = secondary_koji_config

    logging.config.dictConfig(config['logging'])

    global __config
    __config = config


NO_DEFAULT = object()


def get_config(key, default=NO_DEFAULT):
    """
    Gets a value from configuration.

    :param: key
            a dot separated path of configuration keys from the top level.
            (i.e. "database_config.username")
    :param: default
            value to be returned if key is not found
    :raises: KeyError if key is not found and no default was supplied
    :return: configuration value
    """
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
    # pylint: disable=unreachable
    return key  # makes pylint stop thinking the return type must be a dict


def get_koji_config(koji_id, key):
    """
    Wrapper around get_config that first selects particular koji configuration

    :param: koji_id
            name of koji instance, currently only "primary" and "secondary"
    :param: key
            as in get_config, but relative to given koji_config
    """
    if koji_id == 'primary':
        return get_config('koji_config.{}'.format(key))
    elif koji_id == 'secondary':
        return get_config('secondary_koji_config.{}'.format(key))
    raise RuntimeError("Koji ID not found: {}".format(koji_id))
