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

from unittest import skipIf

import hawkey
import koji
from contextlib import contextmanager
from mock import Mock, patch

from test.common import DBTest, RepoCacheMock, rpmvercmp
from koschei import plugin
from koschei.db import RpmEVR
from koschei.backend import koji_util
from koschei.backend.services.repo_resolver import RepoResolver
from koschei.backend.services.build_resolver import BuildResolver
from koschei.models import (
    Dependency, UnappliedChange, Package, ResolutionProblem,
    BuildrootProblem, ResolutionChange, Build,
)

MINIMAL_HAWKEY_VERSION = '0.6.2'

FOO_DEPS = [
    ('A', 0, '1', '1.fc22', 'x86_64'),
    ('B', 0, '4.1', '2.fc22', 'noarch'),
    ('C', 1, '3', '1.fc22', 'x86_64'),
    ('D', 2, '8.b', '1.rc1.fc22', 'x86_64'),
    ('E', 0, '0.1', '1.fc22.1', 'noarch'),
    ('F', 0, '1', '1.fc22', 'noarch'),
    ('R', 0, '3.3', '2.fc22', 'x86_64'),  # in build group
]

REPO = {
    'id': 123,
    'state': 1,
}


def get_sack():
    desc = koji_util.KojiRepoDescriptor(koji_id='primary', repo_id=123, build_tag='f25-build')
    with RepoCacheMock().get_sack(desc) as sack:
        return sack


class ResolverTest(DBTest):
    def setUp(self):
        super(ResolverTest, self).setUp()
        plugin.load_plugins('backend', ['fedmsg'])
        self.repo_resolver = RepoResolver(self.session)
        self.build_resolver = BuildResolver(self.session)
        self.db.commit()

    @contextmanager
    def mocks(self, build_group=['R'], requires=['A', 'F'], repo_id=123,
              repo_available=True):
        repo_info = self.session.koji_mock.repoInfo.return_value = {
            'id': repo_id,
            'tag_name': 'f25-build',
            'state': koji.REPO_READY if repo_available else koji.REPO_DELETED,
        }
        self.session.koji_mock.repoInfo.return_value = repo_info
        self.session.sec_koji_mock.repoInfo.return_value = repo_info
        with patch('koschei.backend.koji_util.get_build_group_cached',
                   return_value=build_group):
            if requires and isinstance(requires[0], str):
                requires = [requires]
            with patch('koschei.backend.koji_util.get_rpm_requires',
                       return_value=requires):
                with patch('koschei.backend.koji_util.get_latest_repo',
                           return_value=repo_info):
                    with patch('fedmsg.publish') as fedmsg_mock:
                        yield fedmsg_mock

    def prepare_foo_build(self, repo_id=123, version='4'):
        self.prepare_packages('foo')
        foo_build = self.prepare_build('foo', True, repo_id=repo_id, resolved=None)
        foo_build.version = version
        foo_build.release = '1.fc22'
        self.db.commit()
        return foo_build

    def assert_collection_fedmsg_emitted(self, fedmsg_mock, prev_state, new_state):
        fedmsg_mock.assert_called_once_with(
            modname='koschei',
            msg={'old': prev_state,
                 'new': new_state,
                 'koji_instance': 'primary',
                 'collection': 'f25',
                 'collection_name': 'Fedora Rawhide',
                 'repo_id': self.collection.latest_repo_id},
            topic='collection.state.change'
        )

    def test_process_build(self):
        old_build = self.prepare_old_build()
        build = self.prepare_foo_build(repo_id=123, version='4')

        with self.mocks():
            self.build_resolver.main()
            self.db.rollback()

        self.assertIs(True, build.deps_resolved)

        expected_changes = [
            (build.id, 'C', 2, RpmEVR(1, '2', '1.fc22'), RpmEVR(1, '3', '1.fc22')),
            (build.id, 'E', 2, None, RpmEVR(0, '0.1', '1.fc22.1')),
        ]
        actual_changes = [
            (c.build_id, c.dep_name, c.distance, c.prev_evr, c.curr_evr)
            for c in build.dependency_changes
        ]
        self.assertCountEqual(expected_changes, actual_changes)

        actual_deps = (
            self.db.query(
                Dependency.name, Dependency.epoch, Dependency.version,
                Dependency.release, Dependency.arch,
            )
            .filter(Dependency.id.in_(build.dependency_keys))
            .all()
        )
        self.assertCountEqual(FOO_DEPS, actual_deps)

        self.assertIsNone(old_build.dependency_keys)

    def test_dont_resolve_against_old_build_when_new_is_running(self):
        foo = self.prepare_packages('foo')[0]
        build = self.prepare_build('foo', False, repo_id=2)
        build.deps_resolved = True
        self.prepare_build('foo', None, repo_id=None)
        with self.mocks(build_group=['gcc', 'bash']):
            self.assertIsNone(self.repo_resolver.get_build_for_comparison(foo))

    def test_skip_unresolved_failed_build(self):
        foo = self.prepare_packages('foo')[0]
        b1 = self.prepare_build('foo', False, repo_id=2, resolved=True)
        self.prepare_build('foo', False, repo_id=3, resolved=False)
        self.db.commit()
        with self.mocks(build_group=['gcc', 'bash']):
            self.assertEqual(b1, self.repo_resolver.get_build_for_comparison(foo))

    def test_dont_resolve_running_build_with_no_repo_id(self):
        foo_build = self.prepare_foo_build()
        foo_build.state = Build.RUNNING
        foo_build.repo_id = None
        with self.mocks():
            self.build_resolver.process_builds(self.collection)
        self.assertIsNone(foo_build.deps_resolved)

    def test_unresolved_build_should_bump_priority(self):
        foo_build = self.prepare_foo_build()
        self.assertEqual(0, foo_build.package.build_priority)
        with self.mocks(requires=['nonexistent', 'A']):
            self.build_resolver.process_builds(self.collection)
        self.assertIs(foo_build.deps_resolved, False)
        self.assertEqual(3000, foo_build.package.build_priority)

    def test_build_repo_deleted(self):
        foo_build = self.prepare_foo_build()
        with self.mocks(repo_available=False):
            self.build_resolver.process_builds(self.collection)
        self.assertIs(foo_build.deps_resolved, False)
        self.assertEqual(3000, foo_build.package.build_priority)

    def test_build_no_build_group(self):
        foo_build = self.prepare_foo_build()
        with self.mocks(build_group=None):
            self.build_resolver.process_builds(self.collection)
        self.assertIs(foo_build.deps_resolved, False)
        self.assertEqual(3000, foo_build.package.build_priority)

    def test_virtual_in_group(self):
        foo_build = self.prepare_foo_build()
        with self.mocks(build_group=['virtual']):
            self.build_resolver.process_builds(self.collection)
        self.assertTrue(foo_build.deps_resolved)

    def test_nonexistent_in_group(self):
        # nonexistent package in build group should be silently ignored
        foo_build = self.prepare_foo_build()
        with self.mocks(build_group=['R', 'nonexistent']):
            self.build_resolver.process_builds(self.collection)
        self.assertTrue(foo_build.deps_resolved)

    def test_resolution_fail(self):
        self.prepare_packages('bar')
        b = self.prepare_build('bar', True, repo_id=123, resolved=False)
        b.epoch = 1
        b.version = '2'
        b.release = '2'
        self.db.commit()
        with self.mocks(requires=['nonexistent']):
            self.build_resolver.process_builds(self.collection)
        self.assertIs(False, b.deps_resolved)

    def prepare_old_build(self):
        old_build = self.prepare_foo_build(repo_id=122, version='3')
        old_build.deps_resolved = True
        old_deps = FOO_DEPS[:]
        old_deps[2] = ('C', 1, '2', '1.fc22', 'x86_64')
        del old_deps[4]  # E
        dependency_keys = []
        for n, e, v, r, a in old_deps:
            dep = Dependency(arch=a, name=n, epoch=e, version=v, release=r)
            self.db.add(dep)
            self.db.flush()
            dependency_keys.append(dep.id)
        old_build.dependency_keys = dependency_keys
        self.db.commit()
        return old_build

    def test_repo_generation(self):
        self.prepare_old_build()
        self.collection.latest_repo_resolved = None
        self.collection.latest_repo_id = None
        self.db.commit()
        with self.mocks(requires=[['F', 'A'], ['nonexistent']]) as fedmsg_mock:
            self.repo_resolver.main()
            self.assertTrue(self.collection.latest_repo_resolved)
            self.assert_collection_fedmsg_emitted(fedmsg_mock, 'unknown', 'ok')
        self.db.expire_all()
        foo = self.db.query(Package).filter_by(name='foo').first()
        self.assertTrue(foo.resolved)
        self.assertEqual(20, foo.dependency_priority)
        expected_changes = [(foo.id, 'C', 1, 1, '2', '3', '1.fc22', '1.fc22', 2),
                            (foo.id, 'E', None, 0, None, '0.1', None, '1.fc22.1', 2)]
        c = UnappliedChange
        actual_changes = self.db.query(c.package_id, c.dep_name, c.prev_epoch,
                                       c.curr_epoch, c.prev_version, c.curr_version,
                                       c.prev_release, c.curr_release, c.distance).all()
        self.assertCountEqual(expected_changes, actual_changes)
        resolution_change = self.db.query(ResolutionChange)\
            .filter_by(package_id=foo.id)\
            .one()
        self.assertFalse(self.db.query(ResolutionProblem)
                         .filter_by(resolution_id=resolution_change.id)
                         .count())
        self.assertTrue(self.collection.latest_repo_resolved)
        self.assertEqual(123, self.collection.latest_repo_id)

    # pylint: disable=too-many-statements
    def test_resolve_newly_added_package(self):
        self.prepare_old_build()
        self.collection.latest_repo_id = REPO['id']
        self.collection.latest_repo_resolved = True
        foo = self.db.query(Package).filter_by(name='foo').first()
        foo.resolved = None
        with self.mocks(requires=[['F', 'A'], ['nonexistent']]):
            self.repo_resolver.main()
        self.assertTrue(foo.resolved)
        self.assertEqual(20, foo.dependency_priority)

    def test_resolve_newly_added_package_unresolved_buildroot(self):
        self.prepare_old_build()
        self.collection.latest_repo_id = REPO['id']
        self.collection.latest_repo_resolved = False
        foo = self.db.query(Package).filter_by(name='foo').first()
        foo.resolved = None
        with self.mocks(requires=[['F', 'A'], ['nonexistent']]):
            self.repo_resolver.main()
        foo = self.db.query(Package).filter_by(name='foo').first()
        self.assertIsNone(foo.resolved)

    def test_result_history(self):
        self.prepare_old_build()
        self.collection.latest_repo_id = None
        self.collection.latest_repo_resolved = None
        self.prepare_group('bar', ['foo'])
        foo = self.db.query(Package).filter_by(name='foo').one()

        # first run, success
        with self.mocks(repo_id=123) as fedmsg_mock:
            self.repo_resolver.main()
            result = self.db.query(ResolutionChange)\
                .filter_by(package_id=foo.id).one()
            self.assertTrue(foo.resolved)
            self.assertTrue(result.resolved)
            self.assertEqual([], result.problems)
            self.assert_collection_fedmsg_emitted(fedmsg_mock, 'unknown', 'ok')

        # second run, fail
        with self.mocks(repo_id=124, requires=['F', 'nonexistent']) as fedmsg_mock:
            self.repo_resolver.main()
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

        # third run, still fail, should not produce additional RR
        with self.mocks(repo_id=125, requires=['F', 'nonexistent']) as fedmsg_mock:
            self.repo_resolver.main()
            self.assertFalse(foo.resolved)
            self.assertEqual(0, self.db.query(ResolutionChange)
                             .filter_by(package_id=foo.id)
                             .filter(ResolutionChange.id > result.id).count())
            self.assertFalse(fedmsg_mock.called)

        # fourth run, fail with different problems, should produce RR
        with self.mocks(repo_id=126, requires=['F', 'getrekt']) as fedmsg_mock:
            self.repo_resolver.main()
            self.assertFalse(foo.resolved)
            result = self.db.query(ResolutionChange).filter_by(package_id=foo.id)\
                .filter(ResolutionChange.id > result.id).one()
            self.assertNotIn('nonexistent', ''.join(map(str, result.problems)))
            self.assertIn('getrekt', ''.join(map(str, result.problems)))
            self.assertFalse(result.resolved)
            self.assertFalse(fedmsg_mock.called)

        # fifth run, back to normal
        with self.mocks(repo_id=127) as fedmsg_mock:
            self.repo_resolver.main()
            result = self.db.query(ResolutionChange).filter_by(package_id=foo.id)\
                .filter(ResolutionChange.id > result.id).one()
            self.assertTrue(foo.resolved)
            self.assertTrue(result.resolved)
            self.assertEqual([], result.problems)
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

        # sixth run, shouldn't produce additional RR
        with self.mocks(repo_id=127) as fedmsg_mock:
            self.repo_resolver.main()
            self.assertIsNone(self.db.query(ResolutionChange)
                              .filter_by(package_id=foo.id)
                              .filter(ResolutionChange.id > result.id)
                              .first())
            self.assertTrue(foo.resolved)
            self.assertFalse(fedmsg_mock.called)

    def test_broken_buildroot(self):
        self.prepare_old_build()
        self.collection.latest_repo_resolved = None
        self.collection.latest_repo_id = None
        self.prepare_packages('bar')
        with self.mocks(repo_id=123, build_group=['bar']) as fedmsg_mock:
            self.repo_resolver.main()
            self.assert_collection_fedmsg_emitted(fedmsg_mock, 'unknown', 'unresolved')
        self.assertFalse(self.collection.latest_repo_resolved)
        self.assertEqual(123, self.collection.latest_repo_id)
        self.assertIn('nonexistent',
                      ''.join(p.problem for p in self.db.query(BuildrootProblem)))

        with self.mocks(repo_id=124) as fedmsg_mock:
            self.repo_resolver.main()
            self.assert_collection_fedmsg_emitted(fedmsg_mock, 'unresolved', 'ok')
        self.assertTrue(self.collection.latest_repo_resolved)
        self.assertEqual(0, self.db.query(BuildrootProblem).count())

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
        with self.mocks():
            sack = get_sack()
            (resolved, problems, deps) = \
                self.repo_resolver.resolve_dependencies(sack, ['/bin/csh'], ['R'])
            self.assertCountEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertCountEqual(['B', 'C', 'R'], [dep.name for dep in deps])

    # qt-x11 requires (sni-qt(x86-64) if plasma-workspace)
    # since plasma-workspace is not installed, sni-qt should not be instaled either
    @skipIf(rpmvercmp(hawkey.VERSION, MINIMAL_HAWKEY_VERSION) < 0,
            'Rich deps are not supported by this hawkey version')
    def test_rich_deps1(self):
        with self.mocks():
            sack = get_sack()
            (resolved, problems, deps) = \
                self.repo_resolver.resolve_dependencies(sack, ['qt-x11'], ['R'])
            self.assertCountEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertCountEqual(['qt-x11', 'R'], [dep.name for dep in deps])

    # qt-x11 requires (sni-qt(x86-64) if plasma-workspace)
    # since plasma-workspace is installed, sni-qt should be instaled too
    @skipIf(rpmvercmp(hawkey.VERSION, MINIMAL_HAWKEY_VERSION) < 0,
            'Rich deps are not supported by this hawkey version')
    def test_rich_deps2(self):
        with self.mocks():
            sack = get_sack()
            (resolved, problems, deps) = \
                self.repo_resolver.resolve_dependencies(sack,
                                                   ['qt-x11', 'plasma-workspace'],
                                                   ['R'])
            self.assertCountEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertCountEqual(['qt-x11', 'plasma-workspace', 'sni-qt', 'R'],
                                 [dep.name for dep in deps])

    # rich deps used directly in BuildRequires
    @skipIf(rpmvercmp(hawkey.VERSION, MINIMAL_HAWKEY_VERSION) < 0,
            'Rich deps are not supported by this hawkey version')
    def test_rich_build_requires(self):
        with self.mocks():
            sack = get_sack()
            (resolved, problems, deps) = \
                self.repo_resolver.resolve_dependencies(sack,
                                                   ['(sni-qt(x86-64) if plasma-workspace)',
                                                    'plasma-workspace'],
                                                   ['R'])
            self.assertCountEqual([], problems)
            self.assertTrue(resolved)
            self.assertIsNotNone(deps)
            self.assertCountEqual(['plasma-workspace', 'sni-qt', 'R'],
                                 [dep.name for dep in deps])
