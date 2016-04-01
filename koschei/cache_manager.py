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

from contextlib import contextmanager
from threading import RLock, Condition, Thread, current_thread


# Print debug information to stderr
class _Logger(object):
    def debug(self, msg):
        # import sys
        # sys.stderr.write(msg + "\n")
        pass
_log = _Logger()


class _Monitor(object):
    def __init__(self, mgr):
        self._mgr = mgr
        self._lock = RLock()
        self._worker_cond = Condition(self._lock)
        self._consumer_cond = Condition(self._lock)
        # Thread which holds lock, or None if not locked
        self._lock_owner = None

    def _acquire_lock(self):
        assert self._lock_owner != current_thread()
        self._lock.acquire()
        assert not self._lock_owner
        self._lock_owner = current_thread()
        self._mgr.sanity_check()

    def _release_lock(self):
        self._mgr.dump()
        self._mgr.sanity_check()
        assert self._lock_owner == current_thread()
        self._lock_owner = None
        self._lock.release()

    @contextmanager
    def locked(self):
        self._acquire_lock()
        try:
            yield
        finally:
            self._release_lock()

    @contextmanager
    def unlocked(self):
        self._release_lock()
        try:
            yield
        finally:
            self._acquire_lock()

    def worker_wait(self):
        assert self._lock_owner == current_thread()
        self._lock_owner = None
        self._worker_cond.wait(1)
        assert not self._lock_owner
        self._lock_owner = current_thread()

    def consumer_wait(self):
        assert self._lock_owner == current_thread()
        self._lock_owner = None
        self._consumer_cond.wait(1)
        assert not self._lock_owner
        self._lock_owner = current_thread()

    def notify_worker(self):
        assert self._lock_owner == current_thread()
        self._worker_cond.notify()

    def notify_all_workers(self):
        assert self._lock_owner == current_thread()
        self._worker_cond.notify_all()

    def notify_consumer(self):
        assert self._lock_owner == current_thread()
        self._consumer_cond.notify()


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

    ALL_STATES = (REQUESTED, PREPARING, PREPARED, ACQUIRED, RELEASED)

    def __init__(self, key, bank):
        self._key = key
        self._value = None
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
        try:
            return self._bank._factory.create(self._key, self._next._value
                                              if self._next else None)
        # pylint: disable=W0702
        except:
            return None

    def _release(self):
        try:
            self._bank._factory.destroy(self._key, self._value)
        # pylint: disable=W0702
        except:
            pass


class _CacheBank(object):
    def __init__(self, bank_id, factory, capacity, max_threads):
        """
        Create cache bank.  Params:
          factory     - object supplied by caller, used to create and
                        destroy item data
          capacity    - max number of items that can be kept in this bank
          max_threads - max number of threads that can be working on
                        producing items for this bank
        """
        self._id = bank_id
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
            if item._index < mru._index:
                item._index = item._index + 1
        mru._index = 0

    def _count_hard(self):
        """ Count hard items (states: PREPARING, PREPARED, ACQUIRED) """
        return sum(1 for item in self._items if item._state != _CacheItem.RELEASED)

    def _count_work(self):
        """ Count items being worked on (state: PREPARING) """
        return sum(1 for item in self._items if item._state == _CacheItem.PREPARING)

    def _count_soft(self):
        """
        Count soft items (states: REQUESTED, PREPARING, PREPARED, ACQUIRED, RELEASED)
        """
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
        self._items = [item for item in self._items if item._key != lru._key]
        for item in self._items:
            if item._index > lru._index:
                item._index = item._index - 1
        lru._release()
        lru._value = None
        lru._transition(_CacheItem.RELEASED, None)

    def _add(self, key, state):
        """ Add new item with specified state """
        item = _CacheItem(key, self)
        item._index = len(self._items)
        item._transition(None, state)
        self._items.append(item)
        return item


class CacheManager(object):
    """
    A fairly generic, reusable, multi-threaded, multi-level cache
    manager, which doesn't depend on any other Koshei code.
    """

    def __init__(self, max_threads):
        self._monitor = _Monitor(self)
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
        bank_id = len(self._banks) + 1
        bank = _CacheBank(bank_id, item_factory, capacity, max_threads)
        self._banks.append(bank)

        try:
            initial_cache = item_factory.populate_cache()
        # pylint: disable=W0702
        except:
            initial_cache = None
        if initial_cache:
            for key, value in initial_cache:
                item = bank._add(key, _CacheItem.RELEASED)
                item._value = value
                bank._access(item)
            while bank._count_soft() > capacity:
                bank._discard_lru()

    def _add_requested_items(self):
        if not self._prefetch_q:
            _log.debug("_ari: Empty prefetch queue")
        for key in self._prefetch_q:
            _log.debug("_ari: Processing item {key}".format(key=key))
            item = self._banks[0]._lookup(key)
            if item and item._state != _CacheItem.RELEASED:
                _log.debug("_ari: Item {key} already used, can't add it again"
                           .format(key=key))
                continue
            for bank in self._banks:
                item = bank._lookup(key)
                if item:
                    assert item._state == _CacheItem.RELEASED
                    break
                elif (bank._count_work() >= bank._max_threads or
                      bank._count_hard() >= bank._capacity):
                    _log.debug("_ari: Item {key} can't be added because bank {bank_id}"
                               " has reached capacity or thread limits"
                               .format(key=key, bank_id=bank._id))
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
                        self._monitor.notify_consumer()
                    else:
                        self._monitor.notify_worker()
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
        with self._monitor.locked():
            _log.debug("Worker started")
            while not self._terminate:
                self._add_requested_items()
                item = self._get_item_to_process()
                if not item:
                    _log.debug("Waiting for work...")
                    self._monitor.worker_wait()
                    _log.debug("... done waiting for work")
                    continue
                _log.debug("Processing %s..." % str(item._key))
                item._transition(_CacheItem.REQUESTED, _CacheItem.PREPARING)
                if item._next:
                    item._next._transition(_CacheItem.PREPARED, _CacheItem.ACQUIRED)
                with self._monitor.unlocked():
                    value = item._prepare()
                item._value = value
                item._transition(_CacheItem.PREPARING, _CacheItem.PREPARED)
                if item._next:
                    item._next._transition(_CacheItem.ACQUIRED, _CacheItem.RELEASED)
                    item._next = None
                    self._monitor.notify_worker()
                _log.debug("... done processing %s" % item._key)
                if item._bank._id == 1:
                    self._monitor.notify_consumer()
                else:
                    self._monitor.notify_worker()
            _log.debug("Worker terminated")
        _log.debug("Worker exited")

    def prefetch(self, key):
        """
        Request item with specified key to be prefetched into L1 cache
        by background thread
        """
        with self._monitor.locked():
            _log.debug("prefetch(%s)" % str(key))
            self._prefetch_q.append(key)
            self._monitor.notify_worker()
            _log.debug("return prefetch(%s)" % str(key))

    def acquire(self, key):
        """
        Get item with specified key from cache. Blocks until item is available
        in the cache. Deadlock will occur if item is not present and was not
        explicitly prefetched. Item will be kept in cache until released.
        """
        with self._monitor.locked():
            _log.debug("acquire(%s)" % str(key))
            item = self._banks[0]._lookup(key)
            while not item or item._state != _CacheItem.PREPARED:
                _log.debug("Waiting on acquire...")
                self._monitor.consumer_wait()
                item = self._banks[0]._lookup(key)
            _log.debug("... done waiting on acquire")
            item._transition(_CacheItem.PREPARED, _CacheItem.ACQUIRED)
            _log.debug("return acquire(%s)" % str(key))
            return item._value

    def release(self, key):
        """
        Release item so that it can be removed from cache. Most recently used
        items will be kept in cache until space is needed for new items.
        """
        with self._monitor.locked():
            _log.debug("release(%s)" % str(key))
            item = self._banks[0]._lookup(key)
            item._transition(_CacheItem.ACQUIRED, _CacheItem.RELEASED)
            item._bank._access(item)
            self._monitor.notify_worker()
            _log.debug("return release(%s)" % str(key))

    def terminate(self):
        """
        Clean up: terminate all background threads and free all cached items
        (state: RELEASED).
        """
        with self._monitor.locked():
            self._terminate = True
            self._monitor.notify_all_workers()
        for thread in self._threads:
            thread.join()
        with self._monitor.locked():
            for bank in self._banks:
                while bank._count_soft() > 0:
                    bank._discard_lru()
                assert not bank._items

    def sanity_check(self):
        for bank in self._banks:
            assert len(bank._items) <= bank._capacity
            # Item indices are be unique, consecutive integers
            assert not (set(item._index for item in bank._items) ^
                        set(xrange(len(bank._items))))
            is_last_bank = bank == self._banks[-1]
            for item in bank._items:
                assert item._bank == bank
                assert item._key
                assert item._state in _CacheItem.ALL_STATES
                if item._state == _CacheItem.REQUESTED:
                    assert not item._value
                    assert is_last_bank or item._next
                elif item._state == _CacheItem.PREPARING:
                    assert not item._value
                    assert is_last_bank or item._next
                elif item._state == _CacheItem.PREPARED:
                    assert not item._next
                elif item._state == _CacheItem.ACQUIRED:
                    assert not item._next
                elif item._state == _CacheItem.RELEASED:
                    assert not item._next
                else:
                    assert None
                assert not item._next or item._next._bank._id == bank._id + 1

    def dump(self):
        _log.debug("> len(banks)={len_banks}, "
                   "len(prefetch_q)={len_pq}, "
                   "terminate={terminate}"
                   .format(len_banks=len(self._banks),
                           len_pq=len(self._prefetch_q),
                           terminate=self._terminate))
        if self._prefetch_q:
            _log.debug("> prefetch_q: {}"
                       .format(', '.join(str(key) for key in self._prefetch_q)))
        for bank in self._banks:
            _log.debug(">   Bank id={id}, "
                       "capacity={capacity}, "
                       "max_threads={max_threads}, "
                       "len(items)={len_items}"
                       .format(id=bank._id,
                               capacity=bank._capacity,
                               max_threads=bank._max_threads,
                               len_items=len(bank._items)))
            for item in bank._items:
                _log.debug(">     Item state={state}, "
                           "key={key}, "
                           "value={value}, "
                           "index={index}, "
                           "next={next}"
                           .format(state=item._state,
                                   key=item._key,
                                   value=(id(item._value) if item._value else None),
                                   index=item._index,
                                   next=("(state={state}, key={key})"
                                         .format(state=item._next._state,
                                                 key=item._next._key)
                                         if item._next else None)))
