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
import shutil
import librepo
from common import DBTest, testdir, postgres_only
from mock import Mock, patch
from koschei.models import Dependency, DependencyChange, Package, ResolutionProblem
from koschei.resolver import Resolver, GenerateRepoTask, ProcessBuildsTask

FOO_DEPS = [
    ('A', 0, '1', '1.fc22', 'x86_64'),
    ('B', 0, '4.1', '2.fc22', 'noarch'),
    ('C', 1, '3', '1.fc22', 'x86_64'),
    ('D', 2, '8.b', '1.rc1.fc22', 'x86_64'),
    ('E', 0, '0.1', '1.fc22.1', 'noarch'),
    ('F', 0, '1', '1.fc22', 'noarch'),
    ('R', 0, '3.3', '2.fc22', 'x86_64'), # in build group
]

def get_repo(name):
    # hawkey sacks cannot be easily populated from within python and mocking
    # hawkey queries would be too complicated, therefore using real repos
    h = librepo.Handle()
    h.local = True
    h.repotype = librepo.LR_YUMREPO
    h.urls = [os.path.join('repo', name)]
    h.yumdlist = ['primary', 'filelists', 'group']
    return h.perform(librepo.Result())

class ResolverTest(DBTest):
    def __init__(self, *args, **kwargs):
        super(ResolverTest, self).__init__(*args, **kwargs)
        self.repo_mock = None
        self.srpm_mock = None

    def setUp(self):
        super(ResolverTest, self).setUp()
        shutil.copytree(os.path.join(testdir, 'test_repo'), 'repo')
        self.repo_mock = Mock()
        self.repo_mock.get_repos.return_value = {'x86_64': get_repo('x86_64')}
        self.srpm_mock = Mock()
        self.srpm_mock.get_repodata.return_value = get_repo('src')
        self.resolver = Resolver(db=self.s, koji_session=Mock(),
                                 repo_cache=self.repo_mock,
                                 srpm_cache=self.srpm_mock)

    def prepare_foo_build(self, repo_id=666, version='4'):
        self.prepare_packages(['foo'])
        foo_build = self.prepare_builds(foo=True, repo_id=None)[0]
        foo_build.repo_id = repo_id
        foo_build.version = version
        foo_build.release = '1.fc22'
        self.s.commit()
        return foo_build

    def test_dont_resolve_against_old_build_when_new_is_running(self):
        foo = self.prepare_packages(['foo'])[0]
        self.prepare_builds(foo=False, repo_id=2)
        self.prepare_builds(foo=None, repo_id=None)
        self.assertIsNone(self.resolver.create_task(GenerateRepoTask).get_build_for_comparison(foo))

    def test_skip_failed_build_with_no_repo_id(self):
        foo = self.prepare_packages(['foo'])[0]
        b1 = self.prepare_builds(foo=False, repo_id=2)[0]
        self.prepare_builds(foo=False, repo_id=None)
        self.assertEqual(b1, self.resolver.create_task(GenerateRepoTask).get_build_for_comparison(foo))

    def test_resolve_build(self):
        foo_build = self.prepare_foo_build()
        package_id = foo_build.package_id
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.create_task(ProcessBuildsTask).run()
        self.repo_mock.get_repos.assert_called_once_with(666)
        self.srpm_mock.get_srpm.assert_called_once_with('foo', None, '4', '1.fc22')
        self.srpm_mock.get_repodata.assert_called_once_with()
        expected_deps = [tuple([package_id, 666] + list(nevr)) for nevr in FOO_DEPS]
        actual_deps = self.s.query(Dependency.package_id, Dependency.repo_id,
                                   Dependency.name, Dependency.epoch,
                                   Dependency.version, Dependency.release,
                                   Dependency.arch).all()
        self.assertItemsEqual(expected_deps, actual_deps)

    # def test_resolution_fail(self):
    #     self.prepare_packages(['foo', 'bar'])
    #     bar = self.prepare_builds(bar=True, repo_id=None)[0]
    #     bar.repo_id = 666
    #     bar.epoch = 1
    #     bar.version = '2'
    #     bar.release = '2'
    #     self.s.commit()
    #     with patch('koschei.util.get_build_group', return_value=['R']):
    #         self.resolver.process_builds()
    #     self.repo_mock.get_repos.assert_called_once_with(666)
    #     self.srpm_mock.get_srpm.assert_called_once_with('bar', 1, '2', '2')
    #     self.srpm_mock.get_repodata.assert_called_once_with()
    #     self.assertFalse(self.s.query(Package).get(bar.package_id).resolved)
    #     self.assertFalse(self.s.query(ResolutionProblem).count())

    def prepare_old_build(self):
        old_build = self.prepare_foo_build(repo_id=555, version='3')
        old_build.deps_processed = True
        old_deps = FOO_DEPS[:]
        old_deps[2] = ('C', 1, '2', '1.fc22', 'x86_64')
        del old_deps[4] # E
        package_id = old_build.package_id
        for n, e, v, r, a in old_deps:
            self.s.add(Dependency(package_id=package_id, repo_id=555, arch=a,
                                  name=n, epoch=e, version=v, release=r))
        self.s.commit()
        return old_build

    def verify_changes(self):
        package_id = self.s.query(Package.id).filter_by(name='foo').scalar()
        expected_changes = [(package_id, 'C', 1, 1, '2', '3', '1.fc22', '1.fc22', 2),
                            (package_id, 'E', None, 0, None, '0.1', None, '1.fc22.1', 2)]
        c = DependencyChange
        actual_changes = self.s.query(c.package_id, c.dep_name, c.prev_epoch,
                                      c.curr_epoch, c.prev_version, c.curr_version,
                                      c.prev_release, c.curr_release, c.distance).all()
        self.assertItemsEqual(expected_changes, actual_changes)

    def test_differences(self):
        self.prepare_old_build()
        self.prepare_foo_build(repo_id=666, version='4')
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.create_task(ProcessBuildsTask).run()
        self.verify_changes()

    @postgres_only
    def test_repo_generation(self):
        self.prepare_old_build()
        self.prepare_packages(['bar'])
        task = self.resolver.create_task(GenerateRepoTask)
        task.refresh_latest_builds = lambda _: None
        with patch('koschei.util.get_build_group', return_value=['R']):
            with patch('fedmsg.publish') as fedmsg_mock:
                task.run(666)
                self.assertTrue(fedmsg_mock.called)
        self.s.expire_all()
        foo = self.s.query(Package).filter_by(name='foo').first()
        bar = self.s.query(Package).filter_by(name='bar').first()
        self.repo_mock.get_repos.assert_called_once_with(666)
        self.srpm_mock.get_repodata.assert_called_once_with()
        self.verify_changes()
        self.assertTrue(foo.resolved)
        self.assertFalse(self.s.query(ResolutionProblem)
                         .filter_by(package_id=foo.id).count())
        self.assertFalse(bar.resolved)
        self.assertTrue(self.s.query(ResolutionProblem)
                        .filter_by(package_id=bar.id).count())
