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

import os
import yaml
import importlib
import builtins

from collections import OrderedDict
from textwrap import dedent

from test import testdir


def represent_ordereddict(dumper, data):
    value = [
        (dumper.represent_data(key), dumper.represent_data(value))
        for key, value
        in data.items()
    ]

    return yaml.nodes.MappingNode('tag:yaml.org,2002:map', value)


yaml.add_representer(OrderedDict, represent_ordereddict)


class KojiMock(object):
    def __init__(self, vcr):
        self.__vcr = vcr
        self.__multicall = False
        self.__mcall_list = []

    @property
    def multicall(self):
        return self.__multicall

    @multicall.setter
    def multicall(self, value):
        self.__multicall = value
        if not value:
            self.__mcall_list = []

    def __getattr__(self, key):
        if self.multicall:
            def method(*args, **kwargs):
                self.__mcall_list.append((key, args, kwargs))
        else:
            def method(*args, **kwargs):
                return self.__vcr.make_call(key, args, kwargs)
        return method

    def multiCall(self):
        self.__multicall = False
        result = [
            [getattr(self, method)(*args, **kwargs)]
            for method, args, kwargs in self.__mcall_list
        ]
        self.__mcall_list = []
        return result

    # we monkeypatch this on our koji session
    @property
    def config(self):
        return self.__vcr.koji_session.config


class KojiVCR(object):
    def __init__(self, koji_session, cassettes):
        self.koji_session = koji_session
        self.cassettes = cassettes
        self.recording = False
        self.replay_list = []
        self.record_list = []
        if not cassettes:
            raise RuntimeError("At least one cassette is required")
        for cassette in cassettes:
            path = os.path.join(testdir, 'data', f'{cassette}.yml')
            if os.path.exists(path):
                self.load_cassette(path)
            elif cassette != cassettes[-1]:
                raise RuntimeError(dedent(f"""
                    Cassette {cassette} not found. Only the last cassette can
                    be used for recording.
                """).replace('\n', ' '))
            else:
                self.recording = True
                self.output_cassette = path

    def load_cassette(self, cassette):
        with open(cassette) as fo:
            self.replay_list += yaml.load(fo)

    def find_replay(self, method, args, kwargs):
        for entry in self.replay_list + self.record_list:
            if (
                    entry['method'] == method and
                    entry.get('args', []) == list(args) and
                    entry.get('kwargs', {}) == dict(kwargs)
            ):
                return entry

    def make_call(self, method, args, kwargs):
        replay = self.find_replay(method, args, kwargs)
        if replay:
            if 'exception' in replay:
                module = replay['exception']['type']['module']
                classname = replay['exception']['type']['class']
                if module:
                    module = importlib.import_module(module)
                else:
                    module = builtins
                exc_class = getattr(module, classname)
                return exc_class(*replay['exception']['args'])
            return replay['result']
        if not self.recording:
            arguments = [f'{arg!r}' for arg in args]
            arguments += [f'{key}={value!r}' for key, value in kwargs.items()]
            invocation = f'{method}({", ".join(arguments)})'
            raise RuntimeError(f"Unexpected call in replay mode: {invocation}")
        return self.record_call(method, args, kwargs)

    def record_call(self, method, args, kwargs):
        record_item = OrderedDict({'method': method})
        if args:
            record_item['args'] = list(args)
        if kwargs:
            record_item['kwargs'] = dict(kwargs)
        try:
            record_item['result'] = getattr(self.koji_session, method)(*args, **kwargs)
            self.record_list.append(record_item)
            return record_item['result']
        except Exception as e:
            record_item['exception'] = OrderedDict()
            record_item['exception']['type'] = OrderedDict()
            record_item['exception']['type']['module'] = type(e).__module__
            record_item['exception']['type']['class'] = type(e).__name__
            record_item['exception']['args'] = list(e.args)
            self.record_list.append(record_item)
            raise e

    def write_cassette(self):
        if not self.recording:
            return
        dirname = os.path.dirname(self.output_cassette)
        if not os.path.isdir(dirname):
            os.mkdir(dirname)
        with open(self.output_cassette, 'w') as fo:
            yaml.dump(self.record_list, fo, default_flow_style=False)

    def create_mock(self):
        return KojiMock(self)
