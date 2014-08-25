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
            pkg, _ = self.prepare_basic_data()
            resolver = self.get_resolver()
            goal = resolver.prepare_goal(sack_mock, pkg, [])
            self.assertIsNone(goal)
            get_srpm_mock.assert_called_once_with(sack_mock, 'rnv')
            self.assertFalse(goal_mock.called)
            self.assertFalse(sltr_mock.called)

    def test_group(self):
        with hawkey_mocks() as (goal_mock, get_srpm_mock, sltr_mock, sack_mock):
            pkg, _ = self.prepare_basic_data()
            resolver = self.get_resolver()
            goal = resolver.prepare_goal(sack_mock, pkg, ["rpm-build", "zip"])
            sltr_mock.assert_has_calls(
                    [call(sack_mock)] * 2 + [call.set(name='rpm-build'), call.set(name='zip')],
                    any_order=True)
            goal_mock.assert_has_calls([call().install(select=sltr_mock)] * 2 +
                                              [call().install(get_srpm_mock()),
                                               call(sack_mock)],
                                       any_order=True)
            self.assertIs(goal_mock(), goal)

    def test_store_deps(self):
        deps = [dict(name='expat', epoch=1, version='2.4', release='1.fc22', arch='x86_64'),
                dict(name='expat-devel', epoch=None, version='2.4', release='1.rc1.fc22', arch='x86_64'),
                dict(name='rnv', epoch=0, version='11', release='1.fc22', arch='src')]
        pkgs = [PkgMock(**dep) for dep in deps]
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        resolver.store_dependencies(repo, pkg, pkgs)
        self.assertEquals(2, self.s.query(m.Dependency).count())
        for dep in deps[0], deps[1]:
            self.assertEquals(1, self.s.query(m.Dependency).filter_by(**dep).count())

    def test_resolve_fail(self):
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        sack_mock = Mock()
        with patch.object(resolver, 'prepare_goal') as goal_mock:
            goal_mock().run.return_value = False
            goal_mock.reset()
            with patch.object(resolver, 'set_unresolved') as unresolved:
                with patch.object(resolver, 'set_resolved') as resolved:
                    resolver.resolve_dependencies(sack_mock, repo, pkg, ['bbb'])
                    goal_mock.assert_has_calls([call(sack_mock, pkg, ['bbb']),
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
                        resolver.resolve_dependencies(sack_mock, repo, pkg, ['bbb'])
                        goal_mock.assert_has_calls([call(sack_mock, pkg, ['bbb']),
                                                    call().run(),
                                                    call().list_installs()])
                        self.assertFalse(unresolved.called)
                        resolved.assert_called_once_with(repo, pkg)
                        store.assert_called_once_with(repo, pkg, goal_mock().list_installs())

    def test_resolve_empty(self):
        pkg, repo = self.prepare_repo()
        resolver = self.get_resolver()
        sack_mock = Mock()
        with patch.object(resolver, 'prepare_goal', Mock(return_value=None)):
            resolver.resolve_dependencies(sack_mock, repo, pkg, ['bbb'])
