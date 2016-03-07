# Copyright (C) 2014-2015 Red Hat, Inc.
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

import os
import librepo
import shutil
import logging
import hawkey

from collections import deque
from contextlib import contextmanager
from functools import total_ordering
from koschei.cache_manager import CacheManager

from koschei import util

log = logging.getLogger('koschei.repo_cache')

REPO_404 = 19

@total_ordering
class RepoDescriptor(object):
    def __init__(self, koji_id, build_tag, repo_id):
        self.koji_id = koji_id
        self.build_tag = build_tag
        self.repo_id = repo_id

    @property
    def remote_url(self):
        remote_url = util.koji_configs[self.koji_id]['repo_url']
        assert '{build_tag}' in remote_url
        assert '{repo_id}' in remote_url
        assert '{arch}' in remote_url
        return remote_url

    @staticmethod
    def from_string(name):
        parts = name.split('-')
        if len(parts) < 3 or not parts[-1].isdigit():
            return None
        return RepoDescriptor(parts[0], '-'.join(parts[1:-1]), int(parts[-1]))

    def __str__(self):
        return '{}-{}-{}'.format(self.koji_id, self.build_tag, self.repo_id)

    def __eq__(self, other):
        try:
            return (self.koji_id == other.koji_id
                    and self.build_tag == other.build_tag
                    and self.repo_id == other.repo_id)
        except AttributeError:
            return False

    def __lt__(self, other):
        return self.repo_id < other.repo_id

    def make_url(self, arch):
        return self.remote_url.format(build_tag=self.build_tag,
                                      repo_id=self.repo_id, arch=arch)


class RepoManager(object):
    def __init__(self, repo_dir, arches):
        self._repo_dir = repo_dir
        self._arches = arches

    def _get_repo_dir(self, repo_descriptor):
        return os.path.join(self._repo_dir, str(repo_descriptor))

    # @Override
    def destroy(self, repo_descriptor, _):
        repo_dir = self._get_repo_dir(repo_descriptor)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

    # @Override
    # Download given repo_id from Koji to disk
    def create(self, repo_descriptor, _):
        self.destroy(repo_descriptor, None)
        repo_dir = self._get_repo_dir(repo_descriptor)
        temp_dir = repo_dir + ".tmp"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        try:
            for arch in self._arches:
                log.debug('Downloading repo {} for arch {} from Koji to disk'.
                          format(repo_descriptor, arch))
                h = librepo.Handle()
                arch_repo_dir = os.path.join(temp_dir, arch)
                os.makedirs(arch_repo_dir)
                h.destdir = arch_repo_dir
                # pylint:disable=no-member
                h.repotype = librepo.LR_YUMREPO
                h.urls = [repo_descriptor.make_url(arch)]
                h.yumdlist = ['primary', 'filelists', 'group']
                h.perform(librepo.Result())
            os.rename(temp_dir, repo_dir)
            log.debug('Repo {} successfully downloaded to disk'.format(repo_descriptor))
            return repo_dir
        except librepo.LibrepoException as e:
            if e.args[0] == REPO_404:
                log.debug('Repo {} was not found'.format(repo_descriptor))
                return None
            raise

    # @Override
    def populate_cache(self):
        repos = []
        log.debug('Reading cached repos from disk...')
        for repo_name in os.listdir(self._repo_dir):
            repo_descriptor = RepoDescriptor.from_string(repo_name)
            repo_path = os.path.join(self._repo_dir, repo_name)
            if repo_descriptor:
                log.debug('Adding cached repo: {}'.format(repo_descriptor))
                repos.append((repo_descriptor, repo_path))
            else:
                # Try to remove files that don't look like valid
                # repos, such as incomplete downloads.
                try:
                    if os.path.isdir(repo_path):
                        shutil.rmtree(repo_path)
                    else:
                        os.remove(repo_path)
                    log.debug('Removed bogus file from cache: {}'.format(repo_path))
                except Exception:
                    log.debug('Unable to remove bogus file from cache: {}'
                              .format(repo_path))
        return repos


class SackManager(object):
    def __init__(self, arches, for_arch):
        self._arches = arches
        self._for_arch = for_arch

    # @Override
    def create(self, repo_descriptor, repo_dir):
        """ Load repo from disk into memory as sack """
        if not repo_dir:
            return
        try:
            sack = hawkey.Sack(arch=self._for_arch)
            for arch in self._arches:
                log.debug('Loading repo {} for arch {} from disk into memory'.
                          format(repo_descriptor, arch))
                arch_repo_dir = os.path.join(repo_dir, arch)
                h = librepo.Handle()
                h.local = True
                # pylint:disable=no-member
                h.repotype = librepo.LR_YUMREPO
                h.urls = [arch_repo_dir]
                h.yumdlist = ['primary', 'filelists', 'group']
                repodata = h.perform(librepo.Result()).yum_repo
                repo = hawkey.Repo('{}-{}'.format(repo_descriptor, arch))
                repo.repomd_fn = repodata['repomd']
                repo.primary_fn = repodata['primary']
                repo.filelists_fn = repodata['filelists']
                sack.load_yum_repo(repo, load_filelists=True)
            log.debug('Repo {} successfully loaded into memory'
                      .format(repo_descriptor))
            return sack
        except (librepo.LibrepoException, IOError):
            log.debug('Repo {} could not be loaded'.format(repo_descriptor))

    # @Override
    # Release sack
    def destroy(self, repo_id, sack):
        # Nothing to do - sack will be garbage-collected automatically.
        pass

    # @Override
    # Initially there are no sacks cached
    def populate_cache(self):
        return None


class RepoCache(object):
    def __init__(self,
                 repo_dir=os.path.join(util.config['directories']['cachedir'],
                                       'repodata'),
                 arches=util.config['dependency']['arches'],
                 for_arch=util.config['dependency']['for_arch']):

        cache_l1_capacity = util.config['dependency']['cache_l1_capacity']
        cache_l2_capacity = util.config['dependency']['cache_l2_capacity']
        cache_l1_threads = util.config['dependency']['cache_l1_threads']
        cache_l2_threads = util.config['dependency']['cache_l2_threads']
        cache_threads_max = util.config['dependency']['cache_threads_max']

        sack_manager = SackManager(arches, for_arch)
        repo_manager = RepoManager(repo_dir, arches)

        self.mgr = CacheManager(cache_threads_max)
        self.mgr.add_bank(sack_manager, cache_l1_capacity, cache_l1_threads)
        self.mgr.add_bank(repo_manager, cache_l2_capacity, cache_l2_threads)

        self.prefetch_order = deque()

    def prefetch_repo(self, repo_descriptor):
        self.prefetch_order.append(repo_descriptor)
        self.mgr.prefetch(repo_descriptor)

    @contextmanager
    def get_sack(self, repo_descriptor):
        if (self.prefetch_order.popleft() != repo_descriptor):
            raise AssertionError("Repo prefetch order does not match acquire order")
        yield self.mgr.acquire(repo_descriptor)
        self.mgr.release(repo_descriptor)

    def cleanup(self):
        return self.mgr.terminate()
