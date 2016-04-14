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

import logging
import rpm
import time

from Queue import Queue
from threading import Thread


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


def set_difference(s1, s2, key):
    compset = {key(x) for x in s2}
    return {x for x in s1 if key(x) not in compset}


def compare_evr(evr1, evr2):
    def epoch_to_str(epoch):
        return str(epoch) if epoch is not None else None

    evr1, evr2 = ((epoch_to_str(e), v, r) for (e, v, r) in (evr1, evr2))
    return rpm.labelCompare(evr1, evr2)


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

        logging.debug('{} time: {}'.format(self._name, human_readable_time(self._time)))

        for child in self._children:
            child.display()
