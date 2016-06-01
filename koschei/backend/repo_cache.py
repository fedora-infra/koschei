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
from functools import total_ordering

import hawkey
import librepo

from koschei.config import get_config, get_koji_config

log = logging.getLogger('koschei.repo_cache')

REPO_404 = 19


@total_ordering
class RepoDescriptor(object):
    def __init__(self, koji_id, build_tag, repo_id):
        self.koji_id = koji_id
        self.build_tag = build_tag
        self.repo_id = repo_id

    @staticmethod
    def from_string(name):
        parts = name.split('-')
        if len(parts) < 3 or not parts[-1].isdigit():
            return None
        return RepoDescriptor(parts[0], '-'.join(parts[1:-1]), int(parts[-1]))

    def __str__(self):
        return '{}-{}-{}'.format(self.koji_id, self.build_tag, self.repo_id)

    def __hash__(self):
        return hash((self.koji_id, self.build_tag, self.repo_id))

    def __eq__(self, other):
        try:
            return (self.koji_id == other.koji_id and
                    self.build_tag == other.build_tag and
                    self.repo_id == other.repo_id)
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.repo_id < other.repo_id

    def make_url(self):
        arch = get_config('dependency.repo_arch')
        topurl = get_koji_config(self.koji_id, 'topurl')
        url = '{topurl}/repos/{build_tag}/{repo_id}/{arch}'
        return url.format(topurl=topurl, build_tag=self.build_tag,
                          repo_id=self.repo_id, arch=arch)


class RepoManager(object):
    def __init__(self, repo_dir):
        self._repo_dir = repo_dir

    def _get_repo_dir(self, repo_descriptor):
        return os.path.join(self._repo_dir, str(repo_descriptor))

    def destroy(self, repo_descriptor, _):
        repo_dir = self._get_repo_dir(repo_descriptor)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

    # Download given repo_id from Koji to disk
    def create(self, repo_descriptor, _):
        self.destroy(repo_descriptor, None)
        repo_dir = self._get_repo_dir(repo_descriptor)
        temp_dir = repo_dir + ".tmp"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.mkdir(temp_dir)
        try:
            log.debug('Downloading repo {} from Koji to disk'.
                      format(repo_descriptor))
            h = librepo.Handle()
            h.destdir = temp_dir
            # pylint:disable=no-member
            h.repotype = librepo.LR_YUMREPO
            h.urls = [repo_descriptor.make_url()]
            h.yumdlist = ['primary', 'filelists', 'group', 'group_gz']
            h.perform(librepo.Result())
            os.rename(temp_dir, repo_dir)
            log.debug('Repo {} successfully downloaded to disk'.format(repo_descriptor))
            return repo_dir
        except librepo.LibrepoException as e:
            if e.args[0] == REPO_404:
                log.debug('Repo {} was not found (url={})'.format(repo_descriptor,
                                                                  h.urls[0]))
                return None
            raise

    def populate_cache(self):
        repos = []
        log.debug('Reading cached repos from disk...')
        for repo_name in os.listdir(self._repo_dir):
            repo_descriptor = RepoDescriptor.from_string(repo_name)
            repo_path = os.path.join(self._repo_dir, repo_name)
            if repo_descriptor:
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
        log.debug('Added {} repos from disk'.format(len(repos)))
        return repos


class SackManager(object):
    def create(self, repo_descriptor, repo_dir):
        """ Load repo from disk into memory as sack """
        if not repo_dir:
            return
        try:
            cache_dir = os.path.join(repo_dir, 'cache')
            build_cache = not os.path.exists(cache_dir)
            for_arch = get_config('dependency.resolve_for_arch')
            arch = get_config('dependency.repo_arch')
            if build_cache:
                os.mkdir(cache_dir)
            sack = hawkey.Sack(arch=for_arch, cachedir=cache_dir)
            log.debug('Loading repo {} from disk into memory'.
                      format(repo_descriptor))
            h = librepo.Handle()
            h.local = True
            # pylint:disable=no-member
            h.repotype = librepo.LR_YUMREPO
            h.urls = [repo_dir]
            h.yumdlist = ['primary', 'filelists', 'group']
            repodata = h.perform(librepo.Result()).yum_repo
            repo = hawkey.Repo('{}-{}'.format(repo_descriptor, arch))
            repo.repomd_fn = repodata['repomd']
            repo.primary_fn = repodata['primary']
            repo.filelists_fn = repodata['filelists']
            sack.load_yum_repo(repo, load_filelists=True, build_cache=build_cache)
            log.debug('Repo {} successfully loaded into memory'
                      .format(repo_descriptor))
            build_cache = False
            return sack
        except (librepo.LibrepoException, IOError):
            log.debug('Repo {} could not be loaded'.format(repo_descriptor))
        finally:
            if build_cache and os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)


class RepoCache(object):
    def __init__(self, repo_dir=None):
        if not repo_dir:
            repo_dir = os.path.join(get_config('directories.cachedir'), 'repodata')

        dep_config = get_config('dependency')
        self.cache_l2_capacity = dep_config['cache_l2_capacity']

        self.sack_manager = SackManager()
        self.repo_manager = RepoManager(repo_dir)

        self.repos = OrderedDict(sorted(self.repo_manager.populate_cache()))
        while len(self.repos) > self.cache_l2_capacity:
            repo_descriptor, repo_path = self.repos.popitem(last=False)
            self.repo_manager.destroy(repo_descriptor, repo_path)

    def get_sack(self, repo_descriptor):
        repo_path = self.repos.get(repo_descriptor)
        if repo_path:
            del self.repos[repo_descriptor]
        else:
            assert len(self.repos) <= self.cache_l2_capacity
            while len(self.repos) >= self.cache_l2_capacity:
                repo_descriptor, repo_path = self.repos.popitem(last=False)
                self.repo_manager.destroy(repo_descriptor, repo_path)
            repo_path = self.repo_manager.create(repo_descriptor, None)
        self.repos[repo_descriptor] = repo_path
        return self.sack_manager.create(repo_descriptor, repo_path)
