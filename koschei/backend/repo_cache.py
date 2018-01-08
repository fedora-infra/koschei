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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>
# Author: Michael Simacek <msimacek@redhat.com>

import logging
import os
import contextlib

from koschei.config import get_config
from koschei.backend import repo_util
from koschei.backend.file_cache import FileCache


class CacheVersionMismatch(Exception):
    pass


# pylint: disable=arguments-differ
class RepoCache(FileCache):
    """
    Cache of repo files. Allows concurrent access from multiple processes, but
    not threads.
    """
    def __init__(self):
        self.log = logging.getLogger('koschei.repo_cache.RepoCache')
        self.cachedir = os.path.join(get_config('directories.cachedir'), 'repodata')
        super(RepoCache, self).__init__(
            cachedir=self.cachedir,
            capacity=get_config('dependency.cache_l2_capacity'),
            log=self.log,
        )
        self.locked = []

    # @Override
    def read_item(self, repo_descriptor, cachedir):
        return repo_util.load_sack(cachedir, repo_descriptor)

    # @Override
    def create_item(self, repo_descriptor, cachedir):
        self.log.info('Downloading repo {}'.format(repo_descriptor))

        sack = repo_util.load_sack(cachedir, repo_descriptor, download=True)
        if sack:
            self.log.info('Repo {} was successfully downloaded'
                          .format(repo_descriptor))
        else:
            self.log.info('Repo {} was not found (url={})'
                          .format(repo_descriptor, repo_descriptor.url))
        return sack

    def get_comps_path(self, repo_descriptor):
        """
        Returns path to comps for given repo. The repo first needs to be locked
        by obtaining the sack.
        """
        assert repo_descriptor in self.locked
        return repo_util.get_comps_path(self.cachedir, repo_descriptor)

    @contextlib.contextmanager
    def get_sack(self, repo_descriptor):
        """
        Returns a hawkey.Sack for given repo while holding read lock on it.
        """
        assert repo_descriptor not in self.locked
        with self.get_item(repo_descriptor) as sack:
            self.locked.append(repo_descriptor)
            yield sack
            self.locked.remove(repo_descriptor)

    def get_sack_copy(self, repo_descriptor):
        """
        Gets a copy of a sack, for which a lock is already held.
        """
        assert repo_descriptor in self.locked
        return repo_util.load_sack(self.cachedir, repo_descriptor, download=False)
