import os
import librepo
import contextlib
import unittest

from mock import PropertyMock, Mock, patch, call
from common import AbstractTest
from koschei import repo_cache

@contextlib.contextmanager
def mocks():
    with patch('librepo.Handle') as librepo_mock:
        with patch('hawkey.Sack') as sack_mock:
            with patch('hawkey.Repo'):
                yield librepo_mock(), sack_mock()


class RepoCacheTest(AbstractTest):

    def setUp(self):
        super(RepoCacheTest, self).setUp()
        self.repos = [7, 123, 666, 1024]
        self.descriptors = {}
        for repo in self.repos:
            desc = self.descriptors[repo] = \
                repo_cache.RepoDescriptor('primary', 'tag', repo)
            os.makedirs(os.path.join('repodata', str(desc)))
        os.mkdir('repodata/not-repo')

    def test_read_from_disk(self):
        with mocks() as (librepo_mock, sack_mock):
            cache = repo_cache.RepoCache(arches=['x86_64'])
            cache.prefetch_repo(self.descriptors[666])
            with cache.get_sack(self.descriptors[666]) as sack:
                self.assertIs(True, librepo_mock.local)
                # pylint:disable=no-member
                self.assertIs(librepo.LR_YUMREPO, librepo_mock.repotype)
                self.assertEqual(['primary', 'filelists', 'group'], librepo_mock.yumdlist)
                self.assertEqual(['./repodata/primary-tag-666/x86_64'], librepo_mock.urls)
                self.assertEqual(1, librepo_mock.perform.call_count)
                self.assertIs(sack_mock, sack)

    def test_download(self):
        with mocks() as (librepo_mock, _):
            cache = repo_cache.RepoCache(arches=['x86_64'])
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'y', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'y', 1)):
                self.assertEqual(2, librepo_mock.perform.call_count)

    def test_reuse(self):
        cache = repo_cache.RepoCache(arches=['x86_64'])
        with mocks() as (librepo_mock, _):
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'y', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'y', 1)):
                pass
        with mocks() as (librepo_mock, _):
            cache.prefetch_repo(repo_cache.RepoDescriptor('primary', 'y', 1))
            with cache.get_sack(repo_cache.RepoDescriptor('primary', 'y', 1)):
                self.assertEqual(0, librepo_mock.perform.call_count)
