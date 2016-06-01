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

import librepo
from mock import patch

from test.common import DBTest
from koschei.backend import repo_cache


@contextlib.contextmanager
def mocks():
    with patch('librepo.Handle') as librepo_mock:
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
                repo_cache.RepoDescriptor('primary', 'build_tag', repo)
            os.makedirs(os.path.join('repodata', str(desc)))
        os.mkdir('repodata/not-repo')

    def test_read_from_disk(self):
        with mocks() as (librepo_mock, sack_mock):
            cache = repo_cache.RepoCache()
            cache.prefetch_repo(self.descriptors[666])
            with cache.get_sack(self.descriptors[666]) as sack:
                self.assertIs(True, librepo_mock.local)
                # pylint:disable=no-member
                self.assertIs(librepo.LR_YUMREPO, librepo_mock.repotype)
                self.assertEqual(['primary', 'filelists', 'group'], librepo_mock.yumdlist)
                self.assertEqual('./repodata/primary-build_tag-666',
                                 librepo_mock.urls[0])
                self.assertEqual(1, librepo_mock.perform.call_count)
                self.assertIs(sack_mock, sack)

    def test_download(self):
        with mocks() as (librepo_mock, _):
            cache = repo_cache.RepoCache()
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'build_tag', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'build_tag', 1)):
                self.assertEqual(2, librepo_mock.perform.call_count)

    def test_reuse(self):
        cache = repo_cache.RepoCache()
        with mocks() as (librepo_mock, _):
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'build_tag', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'build_tag', 1)):
                pass
        with mocks() as (librepo_mock, _):
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'build_tag', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'build_tag', 1)):
                # In-memory caching of sacks is not supported any longer
                self.assertEqual(1, librepo_mock.perform.call_count)
