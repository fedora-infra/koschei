# Copyright (C) 2014-2015  Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function

import logging.config
import os
import re
import hawkey
import socket
import time
from Queue import Queue
from threading import Thread

from rpm import labelCompare


def merge_dict(d1, d2):
    ret = d1.copy()
    for k, v in d2.items():
        if k in ret and isinstance(v, dict) and isinstance(ret[k], dict):
            ret[k] = merge_dict(ret[k], v)
        else:
            ret[k] = v
    return ret


DEFAULT_CONFIGS = '/usr/share/koschei/config.cfg:/etc/koschei/config.cfg'
config = {}


def load_config():
    def parse_config(config_path):
        global config
        if os.path.exists(config_path):
            with open(config_path) as config_file:
                code = compile(config_file.read(), config_path, 'exec')
                conf_locals = {}
                exec code in conf_locals
                if 'config' in conf_locals:
                    config = merge_dict(config, conf_locals['config'])
    config_paths = os.environ.get('KOSCHEI_CONFIG', DEFAULT_CONFIGS)
    for config_path in config_paths.split(':'):
        parse_config(config_path)

load_config()
assert config != {}

logging.config.dictConfig(config['logging'])
log = logging.getLogger('koschei.util')

primary_koji_config = config['koji_config']
secondary_koji_config = dict(primary_koji_config)
secondary_koji_config.update(config['secondary_koji_config'])
koji_configs = {
    'primary': primary_koji_config,
    'secondary': secondary_koji_config,
}

secondary_mode = bool(config['secondary_koji_config'])


def chunks(seq, chunk_size=100):
    while seq:
        yield seq[:chunk_size]
        seq = seq[chunk_size:]


class parallel_generator(object):
    sentinel = object()

    def __init__(self, generator, queue_size=1000):
        self.generator = generator
        self.queue = Queue(maxsize=queue_size)
        self.worker_exception = StopIteration
        self.stop_thread = False
        self.thread = Thread(target=self.worker_fn)
        self.thread.daemon = True
        self.thread.start()

    def worker_fn(self):
        try:
            for item in self.generator:
                self.queue.put(item)
                if self.stop_thread:
                    return
        except Exception as e:
            self.worker_exception = e
        finally:
            self.queue.put(self.sentinel)

    def __iter__(self):
        return self

    def next(self):
        item = self.queue.get()
        if item is self.sentinel:
            raise self.worker_exception  # StopIteration in case of success
        return item

    def stop(self):
        self.stop_thread = True


def _get_best_selector(sack, dep):
    # Based on get_best_selector in dnf's subject.py

    def is_glob_pattern(pattern):
        return set(pattern) & set("*[?")

    def first(iterable):
        it = iter(iterable)
        try:
            return next(it)
        except StopIteration:
            return None

    def _nevra_to_selector(sltr, nevra):
        if nevra.name is not None:
            if is_glob_pattern(nevra.name):
                sltr.set(name__glob=nevra.name)
            else:
                sltr.set(name=nevra.name)
        if nevra.version is not None:
            evr = nevra.version
            if nevra.epoch is not None and nevra.epoch > 0:
                evr = "%d:%s" % (nevra.epoch, evr)
            if nevra.release is None:
                sltr.set(version=evr)
            else:
                evr = "%s-%s" % (evr, nevra.release)
                sltr.set(evr=evr)
        if nevra.arch is not None:
            sltr.set(arch=nevra.arch)
        return sltr

    subj = hawkey.Subject(dep)
    sltr = hawkey.Selector(sack)

    nevra = first(subj.nevra_possibilities_real(sack, allow_globs=True))
    if nevra:
        s = _nevra_to_selector(sltr, nevra)
        if len(s.matches()) > 0:
            return s

    # pylint: disable=E1101
    reldep = first(subj.reldep_possibilities_real(sack))
    if reldep:
        dep = str(reldep)
        s = sltr.set(provides=dep)
        if len(s.matches()) > 0:
            return s

    if re.search(r'^\*?/', dep):
        key = "file__glob" if is_glob_pattern(dep) else "file"
        return sltr.set(**{key: dep})

    return sltr


def run_goal(sack, br, group):
    # pylint:disable=E1101
    goal = hawkey.Goal(sack)
    problems = []
    for name in group:
        sltr = _get_best_selector(sack, name)
        # missing packages are silently skipped as in dnf
        goal.install(select=sltr)
    for r in br:
        sltr = _get_best_selector(sack, r)
        # pylint: disable=E1103
        if not sltr.matches():
            problems.append("No package found for: {}".format(r))
        else:
            goal.install(select=sltr)
    if not problems:
        kwargs = {}
        if config['dependency']['ignore_weak_deps']:
            kwargs = {'ignore_weak_deps': True}
        resolved = goal.run(**kwargs)
        return resolved, goal.problems, goal.list_installs() if resolved else None
    return False, problems, None


def compute_dependency_distances(sack, br, deps):
    dep_map = {dep.name: dep for dep in deps}
    visited = set()
    level = 1
    # pylint:disable=E1103
    pkgs_on_level = {x for r in br for x in
                     _get_best_selector(sack, r).matches()}
    while pkgs_on_level:
        for pkg in pkgs_on_level:
            dep = dep_map.get(pkg.name)
            if dep and dep.distance is None:
                dep.distance = level
        level += 1
        if level >= 5:
            break
        reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                   for req in pkg.requires}
        visited.update(pkgs_on_level)
        pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))


def compare_evr(evr1, evr2):
    def epoch_to_str(epoch):
        return str(epoch) if epoch is not None else None
    evr1, evr2 = ((epoch_to_str(e), v, r) for (e, v, r) in (evr1, evr2))
    return labelCompare(evr1, evr2)


def set_difference(s1, s2, key):
    compset = {key(x) for x in s2}
    return {x for x in s1 if key(x) not in compset}


# Utility class for time measurement
class Stopwatch(object):
    def __init__(self, name, parent=None, start=False):
        self._time = 0
        self._name = name
        self._started = False
        self._children = []
        self._parent = parent
        if parent:
            parent._children.append(self)
        if start:
            self.start()

    def start(self):
        assert not self._started
        if self._parent and not self._parent._started:
            self._parent.start()
        self._time = self._time - time.time()
        self._started = True

    def stop(self):
        assert self._started
        for child in self._children:
            if child._started:
                child.stop()
        self._time = self._time + time.time()
        self._started = False

    def reset(self):
        self._started = False
        for child in self._children:
            child.reset()
        self._time = 0

    def display(self):
        assert not self._started

        def human_readable_time(t):
            s = str(t % 60) + " s"
            t = int(t / 60)
            if t:
                s = str(t % 60) + " min " + s
            t = int(t / 60)
            if t:
                s = str(t % 60) + " h " + s
            return s

        log.debug('{} time: {}'.format(self._name, human_readable_time(self._time)))

        for child in self._children:
            child.display()


def sd_notify(msg):
    sock_path = os.environ.get('NOTIFY_SOCKET', None)
    if not sock_path:
        raise RuntimeError("NOTIFY_SOCKET not set")
    if sock_path[0] == '@':
        sock_path = '\0' + sock_path[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg, sock_path)
    finally:
        sock.close()
