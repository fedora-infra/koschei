# Copyright (C) 2015-2016 Red Hat, Inc.
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


# Print debug information to stderr
class _Logger(object):
    def debug(self, msg):
        #import sys
        #sys.stderr.write(msg + "\n")
        pass
_log = _Logger()


class _CacheItem(object):
    """
    Possible cache item states:
       state      description
       PREPARING  being prefetched by worker thread
       PREPARED   prefetched, waiting to be acquired
       ACQUIRED   used by caller, must be kept
       RELEASED   released, but kept in MRU cache
    """
    REQUESTED = "REQUESTED"
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    ACQUIRED = "ACQUIRED"
    RELEASED = "RELEASED"

    def __init__(self, key, bank):
        self._key = key
        self._value = None
        self._next_value = None
        self._index = 0
        self._next = None
        self._bank = bank
        self._state = None

    def _transition(self, prev_state, next_state):
        _log.debug("Item {key} (bank {bank_id}): transition {prev} -> {next}".format(
            bank_id=self._bank._id, key=self._key, prev=prev_state, next=next_state))
        assert self._state == prev_state
        self._state = next_state

    def _prepare(self):
        self._value = self._bank._factory.create(self._key, self._next._value
                                                 if self._next else
                                                 self._next_value)

    def _release(self):
        self._bank._factory.destroy(self._key, self._value)
        self._value = None


class _CacheBank(object):
    def __init__(self, id, factory, capacity, max_threads):
        """
        Create cache bank.  Params:
          factory     - object supplied by caller, used to create and
                        destroy item data
          capacity    - max number of items that can be kept in this bank
          max_threads - max number of threads that can be working on
                        producing items for this bank
        """
        self._id = id
        self._factory = factory
        self._capacity = capacity
        self._max_threads = max_threads
        self._items = []

    def _lookup(self, key):
        """ Find and return item with given key, or None """
        items = [item for item in self._items if item._key == key]
        return items[0] if items else None

    def _access(self, mru):
        """ Mark specified item as MRU (most recently used) """
        for item in self._items:
            item._index = item._index + 1
        mru._index = 0

    def _count_hard(self):
        """ Count hard items (states: PREPARING, PREPARED, ACQUIRED) """
        return sum(1 for item in self._items if item._state != _CacheItem.RELEASED)

    def _count_work(self):
        """ Count items being worked on (state: PREPARING) """
        return sum(1 for item in self._items if item._state == _CacheItem.PREPARING)

    def _count_soft(self):
        """ Count soft items (states: REQUESTED, PREPARING, PREPARED, ACQUIRED, RELEASED) """
        return len(self._items)

    def _lru(self, state):
        """ Find least-recently used item with given state """
        max_idx = 0
        lru = None
        for item in self._items:
            if item._state == state and item._index >= max_idx:
                max_idx = item._index
                lru = item
        return lru

    def _discard_lru(self):
        """ Remove least-recently used item with state RELEASED """
        lru = self._lru(_CacheItem.RELEASED)
        assert lru
        lru._release()
        lru._transition(_CacheItem.RELEASED, None)
        self._items = [item for item in self._items if item._key != lru._key]

    def _add(self, key, state):
        """ Add new item with specified state """
        item = _CacheItem(key, self)
        item._transition(None, state)
        self._items.append(item)
        self._access(item)
        return item


class CacheManager(object):
    """
    A fairly generic, reusable, multi-threaded, multi-level cache
    manager, which doesn't depend on any other Koshei code.
    """

    def __init__(self, max_threads):
        self._lock = RLock()
        self._work_avail = Condition(self._lock)
        self._sack_avail = Condition(self._lock)
        self._banks = []
        self._threads = []
        self._terminate = False
        self._prefetch_q = []

        while len(self._threads) < max_threads:
            thread = Thread(target=self._thread_proc)
            thread.daemon = True
            thread.start()
            self._threads.append(thread)

    def add_bank(self, item_factory, capacity, max_threads):
        id = len(self._banks) + 1
        bank = _CacheBank(id, item_factory, capacity, max_threads)
        self._banks.append(bank)

        initial_cache = item_factory.populate_cache()
        if initial_cache:
            for key, value in initial_cache:
                item = bank._add(key, _CacheItem.RELEASED)
                item._value = value
            while bank._count_soft() > capacity:
                bank._discard_lru()

    def _add_requested_items(self):
        if not self._prefetch_q:
            _log.debug("_ari: Empty prefetch queue")
        for key in self._prefetch_q:
            _log.debug("_ari: Processing item {key}".format(key=key))
            item = self._banks[0]._lookup(key)
            if item and item._state != _CacheItem.RELEASED:
                _log.debug("_ari: Item {key} already used, can't add it again".format(key=key))
                continue
            for bank in self._banks:
                item = bank._lookup(key)
                if item:
                    assert item._state == _CacheItem.RELEASED
                    break
                elif bank._count_work() >= bank._max_threads or bank._count_hard() >= bank._capacity:
                    _log.debug("_ari: Item {key} can't be added because bank {bank_id} has reached"
                               " capacity or thread limits".format(key=key, bank_id=bank._id))
                    return
            prev_item = None
            for bank in self._banks:
                item = bank._lookup(key)
                if item:
                    if prev_item:
                        prev_item._next = item
                    item._transition(_CacheItem.RELEASED, _CacheItem.PREPARED)
                    item._next = None
                    if bank._id == 1:
                        self._sack_avail.notify()
                    else:
                        self._work_avail.notify()
                    break
                while bank._count_soft() >= bank._capacity:
                    bank._discard_lru()
                _log.debug("_ari: Adding item {key} into bank {bank_id}"
                           .format(key=key, bank_id=bank._id))
                item = bank._add(key, _CacheItem.REQUESTED)
                if prev_item:
                    prev_item._next = item
                prev_item = item
            self._prefetch_q.remove(key)

    def _get_item_to_process(self):
        for bank in self._banks:
            for item in bank._items:
                if item._state == _CacheItem.REQUESTED:
                    if item._next and (item._next._state != _CacheItem.PREPARED):
                        continue
                    return item
        return None

    def _thread_proc(self):
        try:
            self._lock.acquire()
            locked = True
            _log.debug("Worker started")
            while not self._terminate:
                self._add_requested_items()
                item = self._get_item_to_process()
                if not item:
                    _log.debug("Waiting for work...")
                    self._work_avail.wait(1)
                    _log.debug("... done waiting for work")
                    continue
                _log.debug("Processing %s..." % str(item._key))
                item._transition(_CacheItem.REQUESTED, _CacheItem.PREPARING)
                if item._next:
                    item._next._transition(_CacheItem.PREPARED, _CacheItem.ACQUIRED)
                self._lock.release()
                locked = False
                item._prepare()
                self._lock.acquire()
                locked = True
                item._transition(_CacheItem.PREPARING, _CacheItem.PREPARED)
                if item._next:
                    item._next._transition(_CacheItem.ACQUIRED, _CacheItem.RELEASED)
                    item._next = None
                    self._work_avail.notify()
                _log.debug("... done processing %s" % item._key)
                if item._bank._id == 1:
                    self._sack_avail.notify()
                else:
                    self._work_avail.notify()
            _log.debug("Worker terminated")
        finally:
            _log.debug("Worker exited")
            if locked:
                self._lock.release()

    def prefetch(self, key):
        """
        Request item with specified key to be prefetched into L1 cache
        by background thread
        """
        try:
            self._lock.acquire()
            _log.debug("prefetch(%s)" % str(key))
            self._prefetch_q.append(key)
            self._work_avail.notify()
        finally:
            _log.debug("return prefetch(%s)" % str(key))
            self._lock.release()

    def acquire(self, key):
        """
        Get item with specified key from cache. Blocks until item is available
        in the cache. Deadlock will occur if item is not present and was not
        explicitly prefetched. Item will be kept in cache until released.
        """
        try:
            self._lock.acquire()
            _log.debug("acquire(%s)" % str(key))
            item = self._banks[0]._lookup(key)
            while not item or item._state != _CacheItem.PREPARED:
                _log.debug("Waiting on acquire...")
                self._sack_avail.wait(1)
                item = self._banks[0]._lookup(key)
            _log.debug("... done waiting on acquire")
            item._transition(_CacheItem.PREPARED, _CacheItem.ACQUIRED)
            return item._value
        finally:
            _log.debug("return acquire(%s)" % str(key))
            self._lock.release()

    def release(self, key):
        """
        Release item so that it can be removed from cache. Most recently used
        items will be kept in cache until space is needed for new items.
        """
        try:
            self._lock.acquire()
            _log.debug("release(%s)" % str(key))
            item = self._banks[0]._lookup(key)
            item._transition(_CacheItem.ACQUIRED, _CacheItem.RELEASED)
            item._bank._access(item)
            self._work_avail.notify()
        finally:
            _log.debug("return release(%s)" % str(key))
            self._lock.release()

    def terminate(self):
        """
        Clean up: terminate all background threads and free all cached items
        (state: RELEASED).
        """
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
