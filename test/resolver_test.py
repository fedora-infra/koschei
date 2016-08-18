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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import os
import shutil
from unittest import skipIf

import hawkey
import koji
import librepo
from mock import Mock, patch

from test.common import DBTest, testdir, KojiMock
from koschei.backend import koji_util
from koschei.backend.services.resolver import Resolver, DependencyCache
from koschei.models import (Dependency, UnappliedChange, AppliedChange, Package,
                            ResolutionProblem, BuildrootProblem, ResolutionChange)

MINIMAL_HAWKEY_VERSION = '0.6.2'

FOO_DEPS = [
    ('A', 0, '1', '1.fc22', 'x86_64'),
    ('B', 0, '4.1', '2.fc22', 'noarch'),
    ('C', 1, '3', '1.fc22', 'x86_64'),
    ('D', 2, '8.b', '1.rc1.fc22', 'x86_64'),
    ('E', 0, '0.1', '1.fc22.1', 'noarch'),
    ('F', 0, '1', '1.fc22', 'noarch'),
    ('R', 0, '3.3', '2.fc22', 'x86_64'), # in build group
]


def get_sack(url='repo'):
    # hawkey sacks cannot be easily populated from within python and mocking
    # hawkey queries would be too complicated, therefore using real repos
    h = librepo.Handle()
    h.local = True
    h.repotype = librepo.LR_YUMREPO
    h.urls = [url]
    h.yumdlist = ['primary']
    repodata = h.perform(librepo.Result()).yum_repo
    repo = hawkey.Repo('test')
    repo.repomd_fn = repodata['repomd']
    repo.primary_fn = repodata['primary']
    sack = hawkey.Sack(arch='x86_64')
    sack.load_yum_repo(repo)
    return sack


# pylint:disable=unbalanced-tuple-unpacking
class DependencyCacheTest(DBTest):
    def __init__(self, *args, **kwargs):
        super(DependencyCacheTest, self).__init__(*args, **kwargs)

    def dep(self, i):
        return (i, 'foo', 0, str(i), '1', 'x86_64')

    def nevra(self, i):
        return ('foo', 0, str(i), '1', 'x86_64')

    def setUp(self):
        super(DependencyCacheTest, self).setUp()
        deps = [str(self.nevra(i)) for i in range(1, 4)]
        self.db.execute(
            "INSERT INTO dependency(name,epoch,version,release,arch) VALUES {}"
            .format(','.join(deps)))
        self.db.commit()

    def test_get_ids(self):
        cache = DependencyCache(10)
        # from db
        dep1, dep2 = cache.get_by_ids(self.db, [2, 3])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # from cache
        dep1, dep2 = cache.get_by_ids(None, [2, 3])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # mixed
        dep1, dep2 = cache.get_by_ids(self.db, [2, 1])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(1), dep2)
        hash(dep1)
        hash(dep2)

    def test_get_nevras(self):
        cache = DependencyCache(10)
        # from db
        dep1, dep2 = cache.get_or_create_nevras(self.db, [self.nevra(2), self.nevra(3)])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # from cache
        dep1, dep2 = cache.get_or_create_nevras(None, [self.nevra(2), self.nevra(3)])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # insert
        dep1, dep2 = cache.get_or_create_nevras(self.db, [self.nevra(4), self.nevra(5)])
        self.assertEquals(self.dep(4), dep1)
        self.assertEquals(self.dep(5), dep2)
        hash(dep1)
        hash(dep2)
        # mixed
        dep1, dep2, dep3 = cache.get_or_create_nevras(self.db, [self.nevra(6),
                                                                self.nevra(4),
                                                                self.nevra(2)])
        self.assertEquals(self.dep(6), dep1)
        self.assertEquals(self.dep(4), dep2)
        self.assertEquals(self.dep(2), dep3)
        hash(dep1)
        hash(dep2)
        hash(dep3)

        # now get them by id
        dep1, dep2, dep3 = cache.get_by_ids(self.db, [dep1.id, dep2.id, dep3.id])
        self.assertEquals(self.dep(6), dep1)
        self.assertEquals(self.dep(4), dep2)
        self.assertEquals(self.dep(2), dep3)
        hash(dep1)
        hash(dep2)
        hash(dep3)

    def test_lru(self):
        cache = DependencyCache(2)
        # from db
        cache.get_by_ids(self.db, [2, 3])
        # one more
        cache.get_by_ids(self.db, [1])
        # from cache
        cache.get_by_ids(None, [3])
        self.db.query(Dependency).filter_by(version="2").update({'name': 'bar'})
        self.db.commit()
        # refetch
        dep = cache.get_by_ids(self.db, [2])[0]
        self.assertEquals('bar', dep.name)
        # still cached
        cache.get_by_ids(None, [3])

    def test_lru2(self):
        cache = DependencyCache(2)
        # from db
        dep1, dep2 = cache.get_or_create_nevras(self.db, [self.nevra(2), self.nevra(3)])
        self.assertEquals(self.dep(2), dep1)
        self.assertEquals(self.dep(3), dep2)
        # one mode
        dep1 = cache.get_or_create_nevras(self.db, [self.nevra(1)])[0]
        self.assertEquals(self.dep(1), dep1)
        hash(dep1)
        # from cache
        dep3 = cache.get_or_create_nevras(None, [self.nevra(3)])[0]
        self.assertEquals(self.dep(3), dep3)
        hash(dep3)

        self.db.query(Dependency).filter_by(version="2").update({'name': 'bar'})
        self.db.commit()
        # 2 should be expired, this should insert new
        dep = cache.get_or_create_nevras(self.db, [self.nevra(2)])[0]
        self.assertEquals('foo', dep.name)
        hash(dep)
        # still cached
        dep3 = cache.get_or_create_nevras(None, [self.nevra(3)])[0]
        self.assertEquals(self.dep(3), dep3)
        hash(dep3)


class ResolverTest(DBTest):
    def __init__(self, *args, **kwargs):
        super(ResolverTest, self).__init__(*args, **kwargs)
        self.repo_mock = None

    def setUp(self):
        super(ResolverTest, self).setUp()
        shutil.copytree(os.path.join(testdir, 'test_repo'), 'repo')
        self.repo_mock = Mock()
        self.repo_mock.get_sack.return_value = get_sack()
        self.koji_mock = KojiMock()
        self.koji_mock.repoInfo.return_value = {'id': 123, 'tag_name': 'f24-build',
                                                'state': koji.REPO_STATES['READY']}
        self.sec_koji_mock = KojiMock()
        self.sec_koji_mock.repoInfo.return_value = {'id': 123, 'tag_name': 'f24-build',
                                                    'state': koji.REPO_STATES['READY']}
        self.resolver = Resolver(db=self.db,
                                 koji_sessions={'primary': self.koji_mock,
                                                'secondary': self.sec_koji_mock},
                                 repo_cache=self.repo_mock)

    def prepare_foo_build(self, repo_id=666, version='4'):
        self.prepare_packages(['foo'])
        foo_build = self.prepare_build('foo', True, repo_id=repo_id, resolved=None)
        foo_build.version = version
        foo_build.release = '1.fc22'
        self.db.commit()
        return foo_build

    def test_dont_resolve_against_old_build_when_new_is_running(self):
        foo = self.prepare_packages(['foo'])[0]
        build = self.prepare_build('foo', False, repo_id=2)
        build.deps_resolved = True
        self.prepare_build('foo', None, repo_id=None)
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['gcc', 'bash']):
            self.assertIsNone(self.resolver.get_build_for_comparison(foo))

    def test_skip_unresolved_failed_build(self):
        foo = self.prepare_packages(['foo'])[0]
        b1 = self.prepare_build('foo', False, repo_id=2, resolved=True)
        self.prepare_build('foo', False, repo_id=3, resolved=False)
        self.db.commit()
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['gcc', 'bash']):
            self.assertEqual(b1, self.resolver.get_build_for_comparison(foo))

    def test_resolve_build(self):
        foo_build = self.prepare_foo_build()
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['R']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['F', 'A']]):
                self.resolver.process_builds(self.collection)
        actual_deps = self.db.query(Dependency.name, Dependency.epoch,
                                    Dependency.version, Dependency.release,
                                    Dependency.arch)\
            .filter(Dependency.id.in_(foo_build.dependency_keys)).all()
        self.assertItemsEqual(FOO_DEPS, actual_deps)

    def test_virtual_in_group(self):
        foo_build = self.prepare_foo_build()
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['virtual']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['F', 'A']]):
                self.resolver.process_builds(self.collection)
        self.assertTrue(foo_build.deps_resolved)

    def test_resolution_fail(self):
        self.prepare_packages(['bar'])
        b = self.prepare_build('bar', True, repo_id=666, resolved=False)
        b.epoch = 1
        b.version = '2'
        b.release = '2'
        self.db.commit()
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['R']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['nonexistent']]):
                self.resolver.process_builds(self.collection)
        self.assertIs(False, b.deps_resolved)

    def prepare_old_build(self):
        old_build = self.prepare_foo_build(repo_id=555, version='3')
        old_build.deps_resolved = True
        old_deps = FOO_DEPS[:]
        old_deps[2] = ('C', 1, '2', '1.fc22', 'x86_64')
        del old_deps[4] # E
        dependency_keys = []
        for n, e, v, r, a in old_deps:
            dep = Dependency(arch=a, name=n, epoch=e, version=v, release=r)
            self.db.add(dep)
            self.db.flush()
            dependency_keys.append(dep.id)
        old_build.dependency_keys = dependency_keys
        self.db.commit()
        return old_build

    def test_differences(self):
        self.prepare_old_build()
        build = self.prepare_foo_build(repo_id=666, version='4')
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['R']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['F', 'A']]):
                self.resolver.process_builds(self.collection)
        expected_changes = [(build.id, 'C', 1, 1, '2', '3', '1.fc22', '1.fc22', 2),
                            (build.id, 'E', None, 0, None, '0.1', None, '1.fc22.1', 2)]
        c = AppliedChange
        actual_changes = self.db.query(c.build_id, c.dep_name, c.prev_epoch,
                                       c.curr_epoch, c.prev_version, c.curr_version,
                                       c.prev_release, c.curr_release, c.distance).all()
        self.assertItemsEqual(expected_changes, actual_changes)

    def test_repo_generation(self):
        self.prepare_old_build()
        with patch('koschei.backend.koji_util.get_build_group', return_value=['R']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['F', 'A'], ['nonexistent']]):
                self.resolver.generate_repo(self.collection, 666)
        self.db.expire_all()
        foo = self.db.query(Package).filter_by(name='foo').first()
        self.assertTrue(foo.resolved)
        expected_changes = [(foo.id, 'C', 1, 1, '2', '3', '1.fc22', '1.fc22', 2),
                            (foo.id, 'E', None, 0, None, '0.1', None, '1.fc22.1', 2)]
        c = UnappliedChange
        actual_changes = self.db.query(c.package_id, c.dep_name, c.prev_epoch,
                                       c.curr_epoch, c.prev_version, c.curr_version,
                                       c.prev_release, c.curr_release, c.distance).all()
        self.assertItemsEqual(expected_changes, actual_changes)
        resolution_id = self.db.query(ResolutionChange.id)\
            .filter_by(package_id=foo.id).subquery()
        self.assertFalse(self.db.query(ResolutionProblem)
                         .filter_by(resolution_id=resolution_id).count())
        self.assertTrue(self.collection.latest_repo_resolved)
        self.assertEqual(666, self.collection.latest_repo_id)

    def test_result_history(self):
        self.prepare_old_build()
        self.prepare_group('bar', ['foo'])
        with patch('koschei.backend.koji_util.get_build_group', return_value=['R']):
            with patch('fedmsg.publish') as fedmsg_mock:
                # first run, success
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'A']]):
                    self.resolver.generate_repo(self.collection, 666)
                foo = self.db.query(Package).filter_by(name='foo').one()
                result = self.db.query(ResolutionChange)\
                    .filter_by(package_id=foo.id).one()
                self.assertTrue(foo.resolved)
                self.assertTrue(result.resolved)
                self.assertEqual([], result.problems)

                # second run, fail
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'nonexistent']]):
                    self.resolver.generate_repo(self.collection, 667)
                foo = self.db.query(Package).filter_by(name='foo').one()
                result = self.db.query(ResolutionChange).filter_by(package_id=foo.id)\
                    .filter(ResolutionChange.id > result.id).one()
                self.assertFalse(foo.resolved)
                self.assertFalse(result.resolved)
                self.assertIn('nonexistent', ''.join(map(str, result.problems)))
                fedmsg_mock.assert_called_once_with(
                    modname='koschei',
                    msg={'koji_instance': 'primary',
                         'repo': 'f25',
                         'old': 'ok',
                         'name': 'foo',
                         'groups': ['bar'],
                         'collection_name':
                         'Fedora Rawhide',
                         'new': 'unresolved',
                         'collection': 'f25'},
                    topic='package.state.change',
                )
                fedmsg_mock.reset_mock()

                # third run, still fail, should not produce additional RR
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'nonexistent']]):
                    self.resolver.generate_repo(self.collection, 668)
                foo = self.db.query(Package).filter_by(name='foo').one()
                self.assertFalse(foo.resolved)
                self.assertEquals(0, self.db.query(ResolutionChange)
                                  .filter_by(package_id=foo.id)
                                  .filter(ResolutionChange.id > result.id).count())
                self.assertFalse(fedmsg_mock.called)

                # fourth run, fail with different problems, should produce RR
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'getrekt']]):
                    self.resolver.generate_repo(self.collection, 669)
                foo = self.db.query(Package).filter_by(name='foo').one()
                self.assertFalse(foo.resolved)
                result = self.db.query(ResolutionChange).filter_by(package_id=foo.id)\
                    .filter(ResolutionChange.id > result.id).one()
                self.assertNotIn('nonexistent', ''.join(map(str, result.problems)))
                self.assertIn('getrekt', ''.join(map(str, result.problems)))
                self.assertFalse(result.resolved)
                self.assertFalse(fedmsg_mock.called)

                # fifth run, back to normal
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'A']]):
                    self.resolver.generate_repo(self.collection, 670)
                foo = self.db.query(Package).filter_by(name='foo').one()
                result = self.db.query(ResolutionChange).filter_by(package_id=foo.id)\
                    .filter(ResolutionChange.id > result.id).one()
                self.assertTrue(foo.resolved)
                self.assertTrue(result.resolved)
                self.assertEquals([], result.problems)
                fedmsg_mock.assert_called_once_with(
                    modname='koschei',
                    msg={'koji_instance': 'primary',
                         'repo': 'f25',
                         'old': 'unresolved',
                         'name': 'foo',
                         'groups': ['bar'],
                         'collection_name':
                         'Fedora Rawhide',
                         'new': 'ok',
                         'collection': 'f25'},
                    topic='package.state.change',
                )
                fedmsg_mock.reset_mock()

                # sixth run, shouldn't produce additional RR
                with patch('koschei.backend.koji_util.get_rpm_requires',
                           return_value=[['F', 'A']]):
                    self.resolver.generate_repo(self.collection, 671)
                foo = self.db.query(Package).filter_by(name='foo').one()
                self.assertIsNone(self.db.query(ResolutionChange)
                                  .filter_by(package_id=foo.id)
                                  .filter(ResolutionChange.id > result.id)
                                  .first())
                self.assertTrue(foo.resolved)
                self.assertFalse(fedmsg_mock.called)

    def test_broken_buildroot(self):
        self.prepare_old_build()
        self.prepare_packages(['bar'])
        with patch('koschei.backend.koji_util.get_build_group',
                   return_value=['bar']):
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=[['nonexistent']]):
                with patch('fedmsg.publish') as fedmsg_mock:
                    self.resolver.generate_repo(self.collection, 666)
                    self.assertFalse(fedmsg_mock.called)
        self.assertFalse(self.collection.latest_repo_resolved)
        self.assertEquals(666, self.collection.latest_repo_id)
        self.assertTrue(self.db.query(BuildrootProblem).count())

    def test_buildrequires(self):
        call_result = [{'flags': 0, 'name': 'maven-local', 'type': 0, 'version': ''},
                       {'flags': 0, 'name': 'jetty-toolchain', 'type': 0, 'version': ''},
                       {'flags': 16777226,
                        'name': 'rpmlib(FileDigests)',
                        'type': 0,
                        'version': '4.6.0-1'},
                       {'flags': 16777226,
                        'name': 'rpmlib(CompressedFileNames)',
                        'type': 0,
                        'version': '3.0.4-1'}]
        koji_mock = Mock()
        koji_mock.multiCall = Mock(return_value=[[call_result]])
        inp = dict(name='jetty-schemas', version='3.1', release='3.fc21', arch='src')
        res = [req for req in koji_util.get_rpm_requires(koji_mock, [inp])]
        koji_mock.getRPMDeps.assert_called_once_with(inp, koji.DEP_REQUIRE)
        self.assertEqual(res, [['maven-local', 'jetty-toolchain']])

    def test_virtual_file_provides(self):
        with patch('koschei.backend.koji_util.get_build_group', return_value=['R']):
            sack = get_sack()
            (resolved, problems, deps) = \
                self.resolver.resolve_dependencies(sack, ['/bin/csh'], ['R'])
            self.assertItemsEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertItemsEqual(['B', 'C', 'R'], [dep.name for dep in deps])

    # qt-x11 requires (sni-qt(x86-64) if plasma-workspace)
    # since plasma-workspace is not installed, sni-qt should not be instaled either
    @skipIf(hawkey.VERSION < MINIMAL_HAWKEY_VERSION,
            'Rich deps are not supported by this hawkey version')
    def test_rich_deps(self):
        with patch('koschei.backend.koji_util.get_build_group', return_value=['R']):
            sack = get_sack()
            (resolved, problems, deps) = \
                self.resolver.resolve_dependencies(sack, ['qt-x11'], ['R'])
            self.assertItemsEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertItemsEqual(['qt-x11', 'R'], [dep.name for dep in deps])

    # qt-x11 requires (sni-qt(x86-64) if plasma-workspace)
    # since plasma-workspace is installed, sni-qt should be instaled too
    @skipIf(hawkey.VERSION < MINIMAL_HAWKEY_VERSION,
            'Rich deps are not supported by this hawkey version')
    def test_rich_deps2(self):
        with patch('koschei.backend.koji_util.get_build_group', return_value=['R']):
            sack = get_sack()
            (resolved, problems, deps) = \
                self.resolver.resolve_dependencies(sack,
                                                   ['qt-x11', 'plasma-workspace'],
                                                   ['R'])
            self.assertItemsEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertItemsEqual(['qt-x11', 'plasma-workspace', 'sni-qt', 'R'],
                                  [dep.name for dep in deps])
