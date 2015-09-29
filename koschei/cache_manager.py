# Copyright (C) 2015 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from threading import RLock, Condition, Thread


if False:
    # Print debug information to stderr
    class _StderrLogger(object):
        def debug(self, msg):
            import sys
            sys.stderr.write(msg + "\n")
    _log = _StderrLogger()
elif False:
    # Print debug information to logger
    import logging
    _log = logging.getLogger('koschei.cache_manager')
else:
    # Discard debug information
    class _BitBucketLogger(object):
        def debug(self, msg): pass
    _log = _BitBucketLogger()


# Possible cache item states:
#    state      free  wait  work  description
#    REQUESTED   1     1     0    requested for prefetching
#    PREPARING   0     1     1    being prefetched by worker thread
#    PREPARED    0     0     0    prefetched, waiting to be acquired
#    ACQUIRED    0     1     0    used by caller, must be kept
#    RELEASED    1     0     0    released, but kept in MRU cache

class _CacheItem(object):
    def __init__(self, key, bank):
        self._key = key
        self._value = None
        self._index = 0
        self._next = None
        self._bank = bank
        # initial state: REQUESTED
        self._free = True
        self._wait = True
        self._work = False

    def _prepare(self):
        self._value = self._bank._factory.create(self._key, self._next._value if self._next else None)

    def _release(self):
        self._bank._factory.destroy(self._key, self._value)
        self._value = None


class _CacheBank(object):
    # Create cache bank.  Params:
    #   factory     - object supplied by caller, used to create and
    #                 destroy item data
    #   capacity    - max number of items that can be kept in this bank
    #   max_threads - max number of threads that can be working on
    #                 producing items for this bank
    def __init__(self, factory, capacity, max_threads):
        self._factory = factory
        self._capacity = capacity
        self._max_threads = max_threads
        self._items = []

    # Find and return item with given key, or None
    def _lookup(self, key):
        items = [item for item in self._items if item._key == key]
        return items[0] if items else None

    # Mark specified item as MRU (most recently used)
    def _access(self, mru):
        for item in self._items:
            item._index = item._index + 1
        mru._index = 0

    # Count requested items (state: REQUESTED)
    def _count_requested(self):
        return sum(1 for item in self._items if item._free and item._wait)

    # Count hard items (states: PREPARING, PREPARED, ACQUIRED)
    def _count_hard(self):
        return sum(1 for item in self._items if not item._free)

    # Count items being worked on (state: PREPARING)
    def _count_work(self):
        return sum(1 for item in self._items if item._work)

    # Count hard items (states: PREPARING, PREPARED, ACQUIRED, RELEASED)
    def _count_soft(self):
        return len(self._items) - self._count_requested()

    # Find least-recently used item (either REQUESTED or RELEASED)
    def _lru(self, wait):
        max_idx = 0
        lru = None
        for item in self._items:
            if item._free and item._wait == wait and item._index >= max_idx:
                max_idx = item._index
                lru = item
        return lru

    # Remove least-recently used item with state RELEASED
    def _discard_lru(self):
        lru = self._lru(False)
        assert lru
        lru._release()
        self._items = [item for item in self._items if item._key != lru._key]

    # Add new REQUESTED item
    def _add(self, key):
        item = _CacheItem(key, self)
        self._items.append(item)
        return item


# A fairly generic, reusable, multi-threaded, multi-level cache
# manager, which doesn't depend on any other Koshei code.
class CacheManager(object):
    def __init__(self, max_threads):
        self._lock = RLock()
        self._work_avail = Condition(self._lock)
        self._sack_avail = Condition(self._lock)
        self._banks = []
        self._threads = []
        self._terminate = False
        self._l1 = None

        while len(self._threads) < max_threads:
            thread = Thread(target=self._thread_proc)
            thread.daemon = True
            thread.start()
            self._threads.append(thread)

    def add_bank(self, item_factory, capacity, max_threads):
        bank = _CacheBank(item_factory, capacity, max_threads)
        if not self._banks:
            self._l1 = bank
        self._banks.append(bank)

        initial_cache = item_factory.populate_cache()
        if initial_cache:
            for key, value in initial_cache:
                item = bank._add(key)
                item._wait = False
                item._value = value
                bank._access(item)
            while bank._count_soft() > 0:
                bank._discard_lru()


    def _get_item_to_process(self):
        for bank in self._banks:
            if not bank._count_requested():
                continue
            item = bank._lru(True)
            if not item:
                continue
            if item._next and (item._next._free or item._next._wait):
                continue
            if bank._count_work() >= bank._max_threads:
                continue
            if bank._count_hard() >= bank._capacity:
                continue
            while bank._count_soft() >= bank._capacity:
                bank._discard_lru()
            return item
        return None

    def _thread_proc(self):
        try:
            self._lock.acquire()
            _log.debug("Worker started")
            while not self._terminate:
                item = self._get_item_to_process()
                if not item:
                    _log.debug("Waiting for work...")
                    self._work_avail.wait(1)
                    _log.debug("... done waiting for work")
                    continue
                # transition from state REQUESTED to PREPARING
                item._free = False
                item._work = True
                if item._next:
                    # transition from state PREPARED to ACQUIRED
                    item._next._wait = True
                _log.debug("Processing %s..." % str(item._key))
                self._lock.release()
                item._prepare()
                self._lock.acquire()
                # transition from state PREPARING to PREPARED
                item._work = False
                item._wait = False
                self._work_avail.notify()
                if item._next:
                    # transition from state ACQUIRED to RELEASED
                    item._next._wait = False
                    item._next._free = True
                _log.debug("... done processing %s" % item._key)
                if item._bank == self._l1:
                    self._sack_avail.notify()
                else:
                    self._work_avail.notify()
            _log.debug("Worker terminated")
        finally:
            _log.debug("Worker exited")
            self._lock.release()

    # Request item with specified key to be prefetched into L1 cache
    # by background thread
    def prefetch(self, key):
        try:
            self._lock.acquire()
            _log.debug("prefetch(%s)" % str(key))
            prev_item = None
            for bank in self._banks:
                item = bank._lookup(key)
                if not item:
                    item = bank._add(key)
                    if prev_item:
                        prev_item._next = item
                    bank._access(item)
                    prev_item = item
                    self._work_avail.notify()
                else:
                    # transition from state RELEASED to PREPARED
                    item._free = False
                    bank._access(item)
                    break
        finally:
            _log.debug("return prefetch(%s)" % str(key))
            self._lock.release()

    # Get item with specified key from cache.  Blokcs until item is
    # available in the cache.  Deadlock will occur if item is not
    # presest and was not explicitly prefetched.  Item will be kept in
    # cache until released.
    def acquire(self, key):
        try:
            self._lock.acquire()
            _log.debug("acquire(%s)" % str(key))
            item = self._l1._lookup(key)
            assert item
            while item._wait:
                _log.debug("Waiting on acquire...")
                self._sack_avail.wait(1)
            _log.debug("... done waiting on acquire")
            # transition from state PREPARED to ACQUIRED
            item._wait = True
            return item._value
        finally:
            _log.debug("return acquire(%s)" % str(key))
            self._lock.release()

    # Release item so that it can be removed from cache.  Most
    # recently used items will be kept in cache until space is needed
    # for new items.
    def release(self, key):
        try:
            self._lock.acquire()
            _log.debug("release(%s)" % str(key))
            item = self._l1._lookup(key)
            # transition from state ACQUIRED to RELEASED
            item._wait = False
            item._free = True
            self._work_avail.notify()
        finally:
            _log.debug("return release(%s)" % str(key))
            self._lock.release()

    # Clean up: terminate all background threads and free all cached
    # items (state: RELEASED).
    def terminate(self):
        try:
            self._lock.acquire()
            self._terminate = True
            self._work_avail.notify_all()
        finally:
            self._lock.release()
        for thread in self._threads:
            thread.join()
        for bank in self._banks:
            while bank._count_soft() > 0:
                bank._discard_lru()
            assert not bank._items
