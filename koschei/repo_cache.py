# Copyright (C) 2014  Red Hat, Inc.
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

import os
import librepo
import shutil
import logging

from koschei import util

log = logging.getLogger('koschei.repo_cache')

REPO_404 = 19


class RepoCache(object):

    def __init__(self, koji_session,
                 repo_dir=util.config['directories']['repodata'],
                 max_repos=util.config['dependency']['repo_cache_items'],
                 remote_repo=util.config['dependency']['remote_repo'],
                 arches=util.config['dependency']['arches'],
                 local=util.config['dependency']['local']):
        self.koji_session = koji_session
        self._repo_dir = repo_dir
        assert max_repos > 2
        self._max_repos = max_repos
        if not local:
            assert '{repo_id}' in remote_repo and '{arch}' in remote_repo
        self._remote_repo = remote_repo
        self._arches = arches
        self._local = local
        self._lru = {}
        self._index = 0
        self._cache = {}

        if not local:
            # registers old repos so they can be deleted when chosen as victims
            existing_repos = []
            for repo in os.listdir(self._repo_dir):
                if repo.isdigit():
                    existing_repos.append(int(repo))
            existing_repos.sort()
            for repo in existing_repos:
                self._load_from_disk(repo)

    def _get_repo_dir(self, repo_id, arch=None):
        if arch:
            return os.path.join(self._repo_dir, str(repo_id), arch)
        return os.path.join(self._repo_dir, str(repo_id))

    def _download_repo(self, repo_id):
        if self._local:
            return
        repos = {}
        build_tag = None
        if '{build_tag}' in self._remote_repo:
            build_tag = self.koji_session.repoInfo(repo_id)['tag_name']
        try:
            for arch in self._arches:
                h = librepo.Handle()
                destdir = self._get_repo_dir(repo_id, arch)
                if os.path.exists(destdir):
                    shutil.rmtree(destdir)
                os.makedirs(destdir)
                h.destdir = destdir
                h.repotype = librepo.LR_YUMREPO
                url = self._remote_repo.format(repo_id=repo_id, arch=arch,
                                               build_tag=build_tag)
                h.urls = [url]
                h.yumdlist = ['primary', 'filelists', 'group']
                log.info("Downloading {arch} repo from {url}".format(arch=arch,
                                                                     url=url))
                result = h.perform(librepo.Result())
                repos[arch] = result
            self._add_repo(repo_id, repos)
            return repos
        except librepo.LibrepoException as e:
            if e.args[0] == REPO_404:
                log.info("Repo id={} not available, skipping".format(repo_id))
                return None
            raise

    def _load_from_disk(self, repo_id):
        try:
            repos = {}
            for arch in self._arches:
                h = librepo.Handle()
                h.local = True
                h.repotype = librepo.LR_YUMREPO
                h.urls = [self._get_repo_dir(repo_id, arch)]
                h.yumdlist = ['primary', 'filelists', 'group']
                repos[arch] = h.perform(librepo.Result())
            self._add_repo(repo_id, repos)
            return repos
        except (librepo.LibrepoException, IOError):
            pass

    def _add_repo(self, repo_id, repos):
        while len(self._cache) + 1 > self._max_repos:
            victim = sorted(self._lru.items(), key=lambda (k, v): (v, k))[0][0]
            del self._cache[victim]
            del self._lru[victim]
            if not self._local:
                shutil.rmtree(self._get_repo_dir(victim))
        self._cache[repo_id] = repos
        self._lru[repo_id] = self._index

    def get_repo(self, repo_id, arch):
        repos = self.get_repos(repo_id)
        if repos:
            return repos.get(arch)

    def get_repos(self, repo_id):
        repo = self._cache.get(repo_id)
        if not repo:
            repo = self._load_from_disk(repo_id)
        if not repo:
            repo = self._download_repo(repo_id)
        if repo:
            self._index += 1
            self._lru[repo_id] = self._index
            return repo
