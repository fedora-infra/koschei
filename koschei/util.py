# Copyright (C) 2014-2016  Red Hat, Inc.
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

from __future__ import print_function, absolute_import

import os
import re
import logging
import rpm
import time
import socket
import fcntl
import errno

from queue import Queue
from threading import Thread
from functools import wraps


def chunks(seq, chunk_size=100):
    while seq:
        yield seq[:chunk_size]
        seq = seq[chunk_size:]


def to_snake_case(name):
    return re.sub(r'([A-Z])', lambda s: '_' + s.group(0).lower(), name)[1:]


def get_evr(build_or_task_info):
    if isinstance(build_or_task_info, dict):
        return (
            build_or_task_info['epoch'],
            build_or_task_info['version'],
            build_or_task_info['release'],
        )

    return (
        build_or_task_info.epoch,
        build_or_task_info.version,
        build_or_task_info.release,
    )


def is_build_newer(current_build, new_build):
    if current_build is None:
        return True
    if new_build is None:
        return False
    return compare_evr(
        get_evr(current_build),
        get_evr(new_build)
    ) < 0


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

    def __next__(self):
        item = self.queue.get()
        if item is self.sentinel:
            raise self.worker_exception  # StopIteration in case of success
        return item

    def stop(self):
        self.stop_thread = True


def set_difference(s1, s2, key):
    compset = {key(x) for x in s2}
    return {x for x in s1 if key(x) not in compset}


def compare_evr(evr1, evr2):
    def epoch_to_str(epoch):
        return str(epoch) if epoch is not None else None

    evr1, evr2 = ((epoch_to_str(e), v, r) for (e, v, r) in (evr1, evr2))
    return rpm.labelCompare(evr1, evr2)


def sd_notify(msg):
    sock_path = os.environ.get('NOTIFY_SOCKET', None)
    if not sock_path:
        raise RuntimeError("NOTIFY_SOCKET not set")
    if sock_path[0] == '@':
        sock_path = '\0' + sock_path[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(msg.encode('utf-8'), sock_path)
    finally:
        sock.close()


def merge_sorted(iterable1, iterable2, key):
    """Merge two sorted iterables."""
    iters = [iter(g) for g in (iterable1, iterable2)]
    heads = [next(iterator, None) for iterator in iters]
    while heads[0] or heads[1]:
        index = 0 if heads[0] and (not heads[1] or key(heads[0]) < key(heads[1])) else 1
        yield heads[index]
        heads[index] = next(iters[index], None)


class FileLock(object):
    """
    File lock object using fcntl locking.
    Doesn't lock the file given, but creates an auxiliary file with .lock
    suffix. Expected to be used as context manager.

    Warning: POSIX locks are not recursive.

    :directory: in which directory to create the lock file
    :name: lock object name, will be appended .lock suffix
    :immediate: whether to lock when initializing, or leave it up to user
    :exclusive: whether the lock will be exclusive or shared
    """
    def __init__(self, directory, name, immediate=True, exclusive=True):
        self.lock_name = '.{0}.lock'.format(name)
        self.lock_path = os.path.join(directory, self.lock_name)
        self.lock_file = None
        self.exclusive = exclusive
        self.exclusive_locked = False
        self.locked = False
        self.log = logging.getLogger(type(self).__name__)
        if immediate:
            self.lock()

    def _get_type_flag(self, exclusive):
        if exclusive is None:
            exclusive = self.exclusive
        return fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH

    def lock(self, exclusive=None, _other_flags=0):
        """
        Locks the lock file (creates the file if doesn't exist)

        :exclusive: overrides exclusive setting for the object
        """
        while True:
            if not self.lock_file:
                self.lock_file = open(self.lock_path, 'a+')
            try:
                fcntl.lockf(self.lock_file.fileno(),
                            self._get_type_flag(exclusive) | _other_flags)
                our_inode = os.fstat(self.lock_file.fileno()).st_ino
                try:
                    disk_inode = os.stat(self.lock_path).st_ino
                except Exception:
                    disk_inode = -1
                if our_inode != disk_inode:
                    # we locked a file that got unlinked in the meantime, try again
                    self.lock_file.close()
                    self.lock_file = None
                    continue
                if exclusive is True or (exclusive is None and self.exclusive is True):
                    self.exclusive_locked = True
                self.locked = True
                self.log.debug("Locked %s (ex=%s)" % (self.lock_path,
                                                      self.exclusive_locked))
                return True
            except Exception as e:
                # for nonblocking
                if getattr(e, 'errno', None) in (errno.EAGAIN, errno.EACCES):
                    return False
                self.unlock()
                raise

    def try_lock(self, exclusive=None):
        """
        Tries to lock like lock method, but doesn't block. Return True when
        locking was successful.
        """
        return self.lock(exclusive=exclusive, _other_flags=fcntl.LOCK_NB)

    def unlock(self):
        """
        Unlock the file.
        """
        if self.exclusive_locked:
            try:
                os.unlink(self.lock_path)
            except Exception:
                pass  # nothing to do about the exception
        self.exclusive_locked = False
        if self.lock_file:
            self.lock_file.close()  # unlocks
        self.lock_file = None
        if self.locked:
            self.log.debug("Unlocked %s" % self.lock_path)
        self.locked = False

    def __del__(self):
        self.unlock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.unlock()


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
        self.log = logging.getLogger('koschei.util.StopWatch.' + name)

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
            t = t // 60
            if t:
                s = str(t % 60) + " min " + s
            t = t // 60
            if t:
                s = str(t % 60) + " h " + s
            return s

        self.log.info('{} time: {}'.format(self._name, human_readable_time(self._time)))

        for child in self._children:
            child.display()


def stopwatch(parent, note=None):
    def decorator(fn):
        name = fn.__name__
        if note:
            name = '{} ({})'.format(name, note)
        watch = Stopwatch(name, parent)

        @wraps(fn)
        def decorated(*args, **kwargs):
            watch.start()
            res = fn(*args, **kwargs)
            watch.stop()
            return res
        return decorated
    return decorator
