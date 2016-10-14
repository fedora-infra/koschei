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
import shutil
from collections import OrderedDict

import hawkey
import librepo

from koschei.config import get_config
from koschei.backend.repo_util import KojiRepoDescriptor

log = logging.getLogger('koschei.repo_cache')

REPO_404 = 19


class RepoCache(object):
    def __init__(self):
        self._repos_dir = os.path.join(get_config('directories.cachedir'), 'repodata')
        self._capacity = get_config('dependency.cache_l2_capacity')
        self._repos = OrderedDict()

        for repo_descriptor, repo_path in sorted(self._read_cache()):
            self._ensure_capacity()
            self._repos[repo_descriptor] = repo_path
        log.debug('Added {} repos from disk'.format(len(self._repos)))

    def _read_cache(self):
        """ Read cached repos from disk. Remove directories which names
        are not parseable as repo descriptor strings. """
        log.debug('Reading cached repos from disk...')
        for repo_descriptor, repo_path in [(KojiRepoDescriptor.from_string(repo_name),
                                            os.path.join(self._repos_dir, repo_name))
                                           for repo_name in os.listdir(self._repos_dir)]:
            if repo_descriptor:
                yield repo_descriptor, repo_path
            elif os.path.exists(repo_path):
                # Remove directories that don't look like valid repos,
                # such as incomplete downloads.
                shutil.rmtree(repo_path)
                log.debug('Removed bogus file from cache: {}'.format(repo_path))

    def _ensure_capacity(self):
        """ Make sure there is enough room for new repo to be added. """
        if len(self._repos) == self._capacity:
            # Adding repo would exceed capacity, discard LRU repo to free space.
            _, repo_path = self._repos.popitem(last=False)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
        assert len(self._repos) < self._capacity

    def _download_repo(self, repo_descriptor, repo_dir):
        """ Download repo from Koji to disk. """
        h = librepo.Handle()
        h.destdir = repo_dir
        # pylint:disable=no-member
        h.repotype = librepo.LR_YUMREPO
        h.urls = [repo_descriptor.url]
        h.yumdlist = ['primary', 'filelists', 'group', 'group_gz']
        h.perform(librepo.Result())

    def _load_sack(self, repo_descriptor, repo_path, build_cache=False):
        """ Load repo from disk into memory as sack. """
        cache_dir = os.path.join(repo_path, 'cache')
        for_arch = get_config('dependency.resolve_for_arch')
        if build_cache:
            os.mkdir(cache_dir)
        sack = hawkey.Sack(arch=for_arch, cachedir=cache_dir)
        h = librepo.Handle()
        h.local = True
        # pylint:disable=no-member
        h.repotype = librepo.LR_YUMREPO
        h.urls = [repo_path]
        h.yumdlist = ['primary', 'filelists', 'group']
        repodata = h.perform(librepo.Result()).yum_repo
        repo = hawkey.Repo(str(repo_descriptor))
        repo.repomd_fn = repodata['repomd']
        repo.primary_fn = repodata['primary']
        repo.filelists_fn = repodata['filelists']
        sack.load_yum_repo(repo, load_filelists=True, build_cache=build_cache)
        return sack

    def get_sack(self, repo_descriptor):
        """ Load repo as hawkey sack.  Returns None if repo was not found on Koji.
        Failures to load repo for any other reason are raised as exceptions.

        Repos are downloaded to temporary directory, which is atomically renamed to
        final repo directory only after sack is successfully loaded and libsolv
        cache created. This is to make sure that nonexistent or invalid repos are
        never present under repo_path, even if process is killed at some point.
        """
        repo_path = self._repos.get(repo_descriptor)
        if repo_path:
            # Mark repo as most-recently used by re-adding it to OrderedDict.
            del self._repos[repo_descriptor]
            self._repos[repo_descriptor] = repo_path
            return self._load_sack(repo_descriptor, repo_path)
        log.debug('Downloading repo {} (cache miss)'.format(repo_descriptor))
        try:
            self._ensure_capacity()
            repo_path = os.path.join(self._repos_dir, str(repo_descriptor))
            temp_dir = repo_path + ".tmp"
            os.mkdir(temp_dir)
            self._download_repo(repo_descriptor, temp_dir)
            sack = self._load_sack(repo_descriptor, temp_dir, build_cache=True)
            os.rename(temp_dir, repo_path)
            self._repos[repo_descriptor] = repo_path
            log.debug('Repo {} was successfully downloaded'.format(repo_descriptor))
            return sack
        except librepo.LibrepoException as e:
            if e.args[0] == REPO_404:
                log.debug('Repo {} was not found'.format(repo_descriptor))
                return None
            raise
