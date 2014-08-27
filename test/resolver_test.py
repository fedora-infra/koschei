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

from common import DBTest
from collections import namedtuple
from contextlib import contextmanager
from mock import Mock, patch, call
from koschei import models as m
from koschei.resolver import Resolver

PkgMock = namedtuple('package', ['name', 'epoch', 'version', 'release', 'arch'])

@contextmanager
def hawkey_mocks():
    goal_mock = Mock()
    with patch('hawkey.Goal', goal_mock):
        with patch('hawkey.Selector') as sltr_mock:
            sltr_mock.return_value = sltr_mock
            sltr_mock.set.return_value = sltr_mock
            with patch('koschei.resolver.get_srpm_pkg') as get_srpm_mock:
                sack_mock = Mock()
                yield goal_mock, get_srpm_mock, sltr_mock, sack_mock

class ResolverTest(DBTest):
    def get_resolver(self):
        return Resolver(db_session=self.s, koji_session=Mock(),
                        repo_cache=Mock(), srpm_cache=Mock())

    def prepare_repo(self):
        pkg, _ = self.prepare_basic_data()
        repo = m.Repo(id=666)
        self.s.add(repo)
        self.s.commit()
        return pkg, repo

    def test_set_reolved(self):
        pkg, repo = self.prepare_repo()
        pkg.state = m.Package.UNRESOLVED
        resolver = self.get_resolver()
        resolver.set_resolved(repo, pkg)
        self.assertEqual(m.Package.OK, pkg.state)
        self.assertTrue(self.s.query(m.ResolutionResult.resolved)
                              .filter_by(repo_id=repo.id, package_id=pkg.id).scalar())

    def test_set_unresolved(self):
        pkg, repo = self.prepare_repo()
        pkg.state = m.Package.OK
        resolver = self.get_resolver()
        problems = {'foo is not installed', "I don't like this package"}
        resolver.set_unresolved(repo, pkg, list(problems))
        self.assertEqual(m.Package.UNRESOLVED, pkg.state)
        result = self.s.query(m.ResolutionResult)\
                       .filter_by(repo_id=repo.id, package_id=pkg.id).one()
        self.assertFalse(result.resolved)
        self.assertItemsEqual([(p,) for p in problems],
                              self.s.query(m.ResolutionProblem.problem)
                              .filter_by(resolution_id=result.id).all())

    def test_no_srpm(self):
        with hawkey_mocks() as (goal_mock, get_srpm_mock, sltr_mock, sack_mock):
            get_srpm_mock.return_value = None
            pkg, repo = self.prepare_repo()
            resolver = self.get_resolver()
            resolver.resolve_dependencies(sack_mock, repo, pkg, [])
            get_srpm_mock.assert_called_once_with(sack_mock, 'rnv')
            self.assertFalse(goal_mock.called)
            self.assertFalse(sltr_mock.called)

    def test_group(self):
        with hawkey_mocks() as (goal_mock, get_srpm_mock, sltr_mock, sack_mock):
            resolver = self.get_resolver()
            goal = resolver.prepare_goal(sack_mock, get_srpm_mock(), ["rpm-build", "zip"])
            sltr_mock.assert_has_calls(
                    [call(sack_mock)] * 2 + [call.set(name='rpm-build'), call.set(name='zip')],
                    any_order=True)
            goal_mock.assert_has_calls([call().install(select=sltr_mock)] * 2 +
                                              [call().install(get_srpm_mock()),
                                               call(sack_mock)],
                                       any_order=True)
            self.assertIs(goal_mock(), goal)

    def test_create_deps(self):
        deps = [dict(name='expat', epoch=1, version='2.4', release='1.fc22', arch='x86_64'),
                dict(name='expat-devel', epoch=None, version='2.4', release='1.rc1.fc22', arch='x86_64'),
                dict(name='rnv', epoch=0, version='11', release='1.fc22', arch='src')]
        pkgs = [PkgMock(**dep) for dep in deps]
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        created = resolver.create_dependencies(repo, pkg, pkgs)
        for dep in created:
            # easier to compare in DB
            self.s.add(dep)
        self.assertEquals(2, len(created))
        for dep in deps[0], deps[1]:
            self.assertEquals(1, self.s.query(m.Dependency).filter_by(**dep).count())

    def test_store_deps(self):
        dicts = [dict(name='expat', epoch=1, version='2.4', release='1.fc22', arch='x86_64'),
                 dict(name='expat-devel', epoch=None, version='2.4', release='1.rc1.fc22', arch='x86_64'),
                 dict(name='rnv', epoch=0, version='11', release='1.fc22', arch='src')]
        deps = [m.Dependency(**d) for d in dicts]
        pkg, _ = self.prepare_repo()
        for dep in deps:
            dep.package_id = pkg.id
        resolver = self.get_resolver()
        resolver.store_dependencies(deps)
        stored = self.s.query(m.Dependency).all()
        self.assertEqual(3, len(stored))
        for dep in dicts:
            self.assertEqual(1, self.s.query(m.Dependency).filter_by(**dep).count())

    # TODO test distance computation

    def test_resolve_fail(self):
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        sack_mock = Mock()
        with patch.object(resolver, 'prepare_goal') as goal_mock:
            goal_mock().run.return_value = False
            goal_mock.reset()
            with patch.object(resolver, 'set_unresolved') as unresolved:
                with patch.object(resolver, 'set_resolved') as resolved:
                    with patch('koschei.resolver.get_srpm_pkg') as get_srpm_mock:
                        resolver.resolve_dependencies(sack_mock, repo, pkg, ['bbb'])
                        goal_mock.assert_has_calls([call(sack_mock, get_srpm_mock(), ['bbb']),
                                                    call().run()])
                        self.assertFalse(resolved.called)
                        unresolved.assert_called_once_with(repo, pkg, goal_mock().problems)

    def test_resolve_success(self):
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        sack_mock = Mock()
        with patch.object(resolver, 'prepare_goal') as goal_mock:
            goal_mock().run.return_value = True
            goal_mock.reset()
            with patch.object(resolver, 'set_unresolved') as unresolved:
                with patch.object(resolver, 'set_resolved') as resolved:
                    with patch.object(resolver, 'store_dependencies') as store:
                        with patch.object(resolver, 'create_dependencies') as create:
                            with patch.object(resolver, 'set_dependency_distances') as distance:
                                with patch('koschei.resolver.get_srpm_pkg') as get_srpm_mock:
                                    resolver.resolve_dependencies(sack_mock, repo, pkg, ['bbb'])
                                    goal_mock.assert_has_calls([call(sack_mock, get_srpm_mock(), ['bbb']),
                                                                call().run(),
                                                                call().list_installs()])
                                    self.assertFalse(unresolved.called)
                                    resolved.assert_called_once_with(repo, pkg)
                                    create.assert_called_once_with(repo, pkg,
                                                                   goal_mock().list_installs())
                                    store.assert_called_once_with(create())
                                    distance.assert_called_once_with(sack_mock, get_srpm_mock(), create())

    def populate_repo(self, repo_id, deps):
        pkg_names = [src for src in deps.keys()]
        pkgs = self.prepare_packages(pkg_names)
        repo = m.Repo(id=repo_id)
        self.s.add(repo)
        self.s.commit()
        for name, targets in deps.items():
            for target in targets:
                dep = m.Dependency(package_id=pkgs[name].id, repo_id=repo_id,
                                   **self.parse_pkg(target))
                self.s.add(dep)
            result = m.ResolutionResult(repo_id=repo_id, package_id=pkgs[name].id,
                                        resolved=targets is not None)
            self.s.add(result)
        self.s.commit()
        return pkgs

    def assert_change_equals(self, prev, curr, change):
        prev = self.parse_pkg(prev)
        curr = self.parse_pkg(curr)
        self.assertEqual(prev['epoch'], change.prev_epoch)
        self.assertEqual(prev['version'], change.prev_version)
        self.assertEqual(prev['release'], change.prev_release)
        self.assertEqual(curr['epoch'], change.curr_epoch)
        self.assertEqual(curr['version'], change.curr_version)
        self.assertEqual(curr['release'], change.curr_release)
        self.assertEqual(curr['name'], change.dep_name)

    def test_dep_diff(self):
        self.populate_repo(1, {'eclipse': ['webkit-2-6',
                                           'eclipse-pde-4.4-21',
                                           'objectweb-asm-5.1-4']})
        self.populate_repo(2, {'eclipse': ['webkit-2-6',
                                           'eclipse-pde-4.4-22',
                                           'objectweb-asm-4.1-4']})
        build = self.prepare_builds(2, eclipse=False)[0]
        resolver = self.get_resolver()
        resolver.compute_dependency_differences(build.package_id, 1, 2, build.id)
        changes = self.s.query(m.DependencyChange).order_by(m.DependencyChange.dep_name).all()
        self.assertEqual(2, len(changes))
        self.assert_change_equals('eclipse-pde-4.4-21', 'eclipse-pde-4.4-22', changes[0])
        self.assertEqual(build.id, changes[0].applied_in_id)
        self.assert_change_equals('objectweb-asm-5.1-4', 'objectweb-asm-4.1-4', changes[1])
        self.assertEqual(build.id, changes[1].applied_in_id)

    def test_clear_changes(self):
        self.populate_repo(1, {'rnv': ['expat-2-4']})
        self.populate_repo(2, {'rnv': ['expat-2-4'],
                               'eclipse': ['webkit-3-9']})
        build = self.prepare_builds(2, rnv=False)[0]
        change1 = m.DependencyChange(dep_name='bbbb', package_id=build.package_id,
                                     applied_in_id=build.id)
        change2 = m.DependencyChange(dep_name='bbbb',
                                     package_id=self.s.query(m.Package.id).filter_by(name='eclipse').scalar())
        change_old = m.DependencyChange(dep_name='gggg', package_id=build.package_id)
        self.s.add(change1)
        self.s.add(change2)
        self.s.add(change_old)
        self.s.commit()
        resolver = self.get_resolver()
        resolver.compute_dependency_differences(build.package_id, 1, 2, None)
        self.assertFalse(self.s.query(m.DependencyChange).get(change_old.id))
        self.assertTrue(self.s.query(m.DependencyChange).get(change1.id))
        self.assertTrue(self.s.query(m.DependencyChange).get(change2.id))
