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

from __future__ import print_function, absolute_import

import threading

from koschei.config import get_config


_cache_creation_lock = threading.Lock()


class KoscheiSession(object):
    def __init__(self):
        self._caches = {}

    def cache(self, cache_id):
        if cache_id not in self._caches:
            import dogpile.cache
            import dogpile.cache.util
            with _cache_creation_lock:
                if cache_id not in self._caches:
                    cache = dogpile.cache.make_region(
                        key_mangler=(
                            lambda key: dogpile.cache.util.sha1_mangle_key(key.encode())
                        ),
                    )
                    cache.configure(**get_config('caching.' + cache_id))
                    self._caches[cache_id] = cache
        return self._caches[cache_id]
