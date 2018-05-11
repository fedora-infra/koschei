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

"""
Provides generic cache of files/directories with locking scheme using POSIX locks.
"""

import logging
import os
import shutil
import json
import contextlib

from koschei.util import FileLock


class CacheVersionMismatch(Exception):
    pass


class CacheExhaustedException(Exception):
    pass


class FileCache(object):
    """
    Abstract cache of files/directories on disk. Allows concurrent access using
    POSIX file locks.

    Specializations need to override read_item and create_item methods.

    Algorithm notes and invariants:
    - entries are stored in an index file
    - an item can be in two states
        - "preparing" - being prepared. It only serves as a placeholder that
          reserves capacity
        - "ready" - ready for being used
    - item locking - locking item ensures that it doesn't change
      state while being inspected:
      - if it's marked as ready, it can be safely read and used
      - if it's marked as preparing or not present in index at all,
        then it's invalid and should be deleted
    - index file is locked to ensure transactionality of operations,
      but it's not a global lock for the cache
    - never block on item lock while holding index lock
    """

    INDEX_VERSION = 1

    def __init__(self, cachedir, capacity, log=None):
        self.log = log or logging.getLogger('koschei.file_cache.FileCache')
        self._cachedir = cachedir
        self._capacity = capacity

    def read_item(self, cache_key, cachedir):
        """
        Reads and returns item with given key from cache. Will be called with
        read lock held.
        """
        raise NotImplementedError()

    def create_item(self, cache_key, cachedir):
        """
        Creates item with given key on disk in cachedir. The file/dir name must
        be str(cache_key). Should return the item created. Will be called with
        write lock held.
        """
        raise NotImplementedError()

    def _p(self, filename):
        """
        Returns path to filename in the cache
        """
        return os.path.join(self._cachedir, filename)

    def _read_index(self, silent=False):
        """
        Reads entries from index file. If the file is invalid or of older
        version, returns empty dict (which will later on cause cache discard)
        Expects the index to be read locked already.
        """
        index_path = self._p('index.json')
        entries = {}
        if os.path.exists(index_path):
            with open(index_path) as index:
                try:
                    index_content = json.load(index)
                    if index_content['version'] > self.INDEX_VERSION:
                        raise CacheVersionMismatch(
                            "Cache index version is newer than current"
                        )
                    elif index_content['version'] < self.INDEX_VERSION:
                        if not silent:
                            self.log.info("Cache index version is old. "
                                          "Discarding cache")
                    else:
                        entries = index_content['entries']
                except CacheVersionMismatch as e:
                    raise
                except Exception as e:
                    if not silent:
                        self.log.warning(
                            "Cannot parse cache index %s, discarding cache",
                            e,
                        )
        return entries

    def _write_index(self, entries):
        """
        Writes entries to the index file.
        Expects it to be exclusively locked already.
        """
        index_path = self._p('index.json')
        with open(index_path + '.tmp', 'w') as index:
            json.dump(dict(version=self.INDEX_VERSION, entries=entries),
                      index, indent=True)
        # would need fsync (or O_PONIES) to be truly atomic, but it's just a cache
        os.rename(index_path + '.tmp', index_path)

    def _cleanup_items(self, entries, exclude):
        """
        Removes all directories that can be locked and don't have corresponding
        entries in ready state.
        Expects index to be exclusively locked. Writes to index file.
        """
        dirents = [d for d in os.listdir(self._cachedir)
                   if os.path.isdir(self._p(d)) and not d == exclude]
        for dirent in dirents:
            if entries.get(dirent) != 'ready':
                with FileLock(self._cachedir, dirent, exclusive=True,
                              immediate=False) as lock:
                    if lock.try_lock():
                        entries.pop(dirent, None)
                        self._write_index(entries)  # preserve atomicity
                        self.log.info("Deleting %s", dirent)
                        shutil.rmtree(self._p(dirent), ignore_errors=True)
        self._write_index(entries)

    @contextlib.contextmanager
    def get_item(self, cache_key):
        key = str(cache_key)

        while True:
            with FileLock(self._cachedir, key, exclusive=True) as item_lock:
                with FileLock(self._cachedir, 'index', exclusive=True) as index_lock:
                    entries = self._read_index()
                    entry = entries.get(key)
                    if entry and entry == 'ready':
                        # other process added it in the meantime
                        index_lock.unlock()
                        # relax the item lock to shared
                        item_lock.lock(exclusive=False)
                        # I haven't found any documentation guaranteeing
                        # relocking to be atomic, so I must assume it isn't
                        index_lock.lock(exclusive=False)
                        entry = self._read_index(silent=True).get(key)
                        if entry and entry == 'ready':
                            index_lock.unlock()
                            yield self.read_item(cache_key, self._cachedir)
                            return
                        # we lost it during relocking
                        continue
                    entries.pop(key, None)
                    # ok, it's definitely not there, we have to add it
                    if len(entries) >= self._capacity:
                        # discard invalid repos
                        self._cleanup_items(entries, exclude=key)
                    if len(entries) >= self._capacity:
                        # discard old repos
                        ready_repos = (k for k, v in entries.items() if v == 'ready')
                        victims = sorted(
                            ready_repos,
                            key=lambda r: os.path.getmtime(self._p(r))
                        )[:len(entries) - self._capacity + 1]
                        for victim in victims:
                            del entries[victim]
                        if victims:
                            # will delete unreferenced items from disk
                            self._cleanup_items(entries, exclude=key)

                    if len(entries) >= self._capacity:
                        raise CacheExhaustedException(
                            "Cannot free space for new cache item. "
                            "Increase the cache size or decrease the number "
                            "of processes using it."
                        )

                    # we have capacity - add new item
                    entries[key] = 'preparing'
                    self._write_index(entries)
                    index_lock.unlock()

                    # perform preparation
                    item = self.create_item(cache_key, self._cachedir)

                    # register item as ready (if it is)
                    index_lock.lock()
                    entries = self._read_index()
                    if item:
                        entries[key] = 'ready'
                        self._write_index(entries)
                    else:
                        entries.pop(key)
                        self._write_index(entries)
                        shutil.rmtree(self._p(key), ignore_errors=True)
                        yield None
                        return
                    index_lock.unlock()
                    # relax the item lock to shared
                    item_lock.lock(exclusive=False)
                    index_lock.lock(exclusive=False)
                    entry = self._read_index(silent=True).get(key)
                    if entry and entry == 'ready':
                        index_lock.unlock()
                        yield item
                        return
                    # while unlikely, we lost the item while relocking
                    continue
