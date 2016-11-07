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

import contextlib
import os
import json
import collections

from mock import patch, Mock

from test.common import DBTest
from koschei.backend import repo_cache
from koschei.backend.repo_util import KojiRepoDescriptor


@contextlib.contextmanager
def mocks():
    with patch('librepo.Handle') as librepo_mock:
        librepo_mock.perform.return_value = collections.defaultdict(Mock)
        with patch('hawkey.Sack') as sack_mock:
            with patch('hawkey.Repo'):
                yield librepo_mock(), sack_mock()


class RepoCacheTest(DBTest):
    def setUp(self):
        super(RepoCacheTest, self).setUp()
        self.repos = [7, 123, 666, 1024]
        self.descriptors = {}
        for repo in self.repos:
            desc = self.descriptors[repo] = \
                KojiRepoDescriptor('primary', 'build_tag', repo)
            os.makedirs(os.path.join('repodata', str(desc)))
        os.mkdir('repodata/not-repo')
        with open('repodata/index.json', 'w') as index:
            json.dump(dict(
                version=repo_cache.RepoCache.INDEX_VERSION,
                entries={str(d): 'ready' for d in self.descriptors.values()},
            ), index)

    def test_read_from_disk(self):
        with patch('koschei.backend.repo_util.load_sack') as load_sack:
            cache = repo_cache.RepoCache()
            with cache.get_sack(self.descriptors[666]) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', self.descriptors[666])
            self.assertIs(load_sack(), sack)

    def test_download(self):
        with patch('koschei.backend.repo_util.load_sack') as load_sack:
            cache = repo_cache.RepoCache()
            desc = KojiRepoDescriptor('primary', 'build_tag', 1)
            with cache.get_sack(desc) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', desc, download=True)
            self.assertIs(load_sack(), sack)

    def test_reuse(self):
        desc = KojiRepoDescriptor('primary', 'build_tag', 1)
        with patch('koschei.backend.repo_util.load_sack') as load_sack:
            cache = repo_cache.RepoCache()
            with cache.get_sack(desc) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', desc, download=True)
            self.assertIs(load_sack(), sack)
            load_sack.reset_mock()
            with cache.get_sack(desc) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', desc)
            self.assertIs(load_sack(), sack)

    def test_reuse_existing(self):
        desc = KojiRepoDescriptor('primary', 'build_tag', 1)
        with patch('koschei.backend.repo_util.load_sack') as load_sack:
            cache = repo_cache.RepoCache()
            with cache.get_sack(desc) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', desc, download=True)
            self.assertIs(load_sack(), sack)
            load_sack.reset_mock()

            # instantiate new cache
            cache = repo_cache.RepoCache()
            with cache.get_sack(desc) as sack:
                pass
            load_sack.assert_called_once_with('./repodata', desc)
            self.assertIs(load_sack(), sack)
