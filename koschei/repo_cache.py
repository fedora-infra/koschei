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

from contextlib import contextmanager
from koschei.cache_manager import CacheManager

from koschei import util

log = logging.getLogger('koschei.repo_cache')

REPO_404 = 19


class RepoManager(object):
    def __init__(self, arches, remote_repo, repo_dir):
        self._arches = arches
        self._remote_repo = remote_repo
        self._repo_dir = repo_dir

    def _get_repo_dir(self, repo_id):
        return os.path.join(self._repo_dir, str(repo_id))

    def _clean_repo_dir(self, repo_id):
        repo_dir = self._get_repo_dir(repo_id)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

    # Download given repo_id from Koji to disk
    def create(self, repo_id, build_tag):
        assert build_tag
        self._clean_repo_dir(repo_id)
        repo_dir = self._get_repo_dir(repo_id)
        temp_dir = repo_dir + ".tmp"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        try:
            for arch in self._arches:
                log.debug('Downloading {} repo {} for arch {} from Koji to disk'.
                          format(build_tag, repo_id, arch))
                h = librepo.Handle()
                arch_repo_dir = os.path.join(temp_dir, arch)
                os.makedirs(arch_repo_dir)
                h.destdir = arch_repo_dir
                h.repotype = librepo.LR_YUMREPO
                url = self._remote_repo.format(repo_id=repo_id, arch=arch,
                                               build_tag=build_tag)
                h.urls = [url]
                h.yumdlist = ['primary', 'filelists', 'group']
                h.perform(librepo.Result())
            os.rename(temp_dir, repo_dir)
            log.debug('Repo {} successfully downloaded to disk'.format(repo_id))
            return repo_dir
        except librepo.LibrepoException as e:
            if e.args[0] == REPO_404:
                log.debug('Repo {} was not found'.format(repo_id))
                return None
            raise

    # Remove repo from disk
    def destroy(self, repo_id, repo):
        self._clean_repo_dir(repo_id)

    # Read list of repo_id's cached on disk
    def populate_cache(self):
        repos = []
        log.debug('Reading cached repos from disk...')
        for repo in os.listdir(self._repo_dir):
            repo_path = self._get_repo_dir(repo)
            if repo.isdigit():
                repo_id = int(repo)
                repos.append((repo_id, repo_path))
            else:
                # Try to remove files that don't look like valid
                # repos, such as incomplete downloads.
                try:
                    if (os.path.isdir(repo_path)):
                        shutil.rmtree(repo_path)
                    else:
                        os.remove(repo_path)
                    log.debug('Removed bogus file from cache: {}'.format(repo_path))
                except:
                    log.debug('Unable to remove bogus file from cache: {}'.format(repo_path))
        return repos


class SackManager(object):
    def __init__(self, arches, for_arch):
        self._arches = arches
        self._for_arch = for_arch

    def create(self, repo_id, repo_dir):
        """ Load repo from disk into memory as sack """
        if not repo_dir:
            return None
        try:
            sack = hawkey.Sack(arch=self._for_arch)
            for arch in self._arches:
                log.debug('Loading repo {} for arch {} from disk into memory'.
                          format(repo_id, arch))
                arch_repo_dir = os.path.join(repo_dir, arch)
                h = librepo.Handle()
                h.local = True
                h.repotype = librepo.LR_YUMREPO
                h.urls = [arch_repo_dir]
                h.yumdlist = ['primary', 'filelists', 'group']
                repodata = h.perform(librepo.Result()).yum_repo
                repo = hawkey.Repo('{}-{}'.format(repo_id, arch))
                repo.repomd_fn = repodata['repomd']
                repo.primary_fn = repodata['primary']
                repo.filelists_fn = repodata['filelists']
                sack.load_yum_repo(repo, load_filelists=True)
            log.debug('Repo {} successfully loaded into memory'.format(repo_id))
            return sack
        except (librepo.LibrepoException, IOError):
            log.debug('Repo {} could not be loaded'.format(repo_id))
            return None

    # Release sack
    def destroy(self, repo_id, sack):
        # Nothing to do - sack will be garbage-collected automatically.
        pass

    # Initially there are no sacks cached
    def populate_cache(self):
        return None


class RepoCache(object):
    def __init__(self,
                 repo_dir=util.config['directories']['repodata'],
                 remote_repo=util.config['dependency']['remote_repo'],
                 arches=util.config['dependency']['arches'],
                 for_arch=util.config['dependency']['for_arch'],
                 cache_l1_capacity=util.config['dependency']['cache_l1_capacity'],
                 cache_l2_capacity=util.config['dependency']['cache_l2_capacity'],
                 cache_l1_threads=util.config['dependency']['cache_l1_threads'],
                 cache_l2_threads=util.config['dependency']['cache_l2_threads'],
                 cache_threads_max=util.config['dependency']['cache_threads_max']):

        sack_manager = SackManager(arches, for_arch)
        repo_manager = RepoManager(arches, remote_repo, repo_dir)

        self.mgr = CacheManager(cache_threads_max)
        self.mgr.add_bank(sack_manager, cache_l1_capacity, cache_l1_threads)
        self.mgr.add_bank(repo_manager, cache_l2_capacity, cache_l2_threads)

    def prefetch_repo(self, repo_id, build_tag):
        self.mgr.prefetch(repo_id, build_tag)

    @contextmanager
    def get_sack(self, repo_id):
        yield self.mgr.acquire(repo_id)
        self.mgr.release(repo_id)

    def cleanup(self):
        return self.mgr.terminate()
