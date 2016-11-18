# Copyright (C) 2016  Red Hat, Inc.
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
import hawkey
import koji

from mock import patch

from test.common import testdir, DBTest, service_ctor, my_vcr
from koschei.models import Build, CoprRebuildRequest
from koschei.backend.repo_util import get_repo


CoprResolver = service_ctor('copr_resolver', 'copr')

REPO_URL = 'http://copr-be-dev.cloud.fedoraproject.org/results/msimacek/'\
    'input/fedora-26-x86_64/'


def get_repo_mock(repo_dir, descriptor, download=False):
    if 'copr' in str(type(descriptor)).lower():
        repo = hawkey.Repo(str(descriptor))
        path = os.path.join(testdir, 'repos', 'copr_repo', 'repodata')
        repo.repomd_fn = os.path.join(path, 'repomd.xml')
        repo.primary_fn = os.path.join(path, 'primary.xml')
        repo.filelists_fn = os.path.join(path, 'filelists.xml')
        return repo
    return get_repo(repo_dir, descriptor, download)


class CoprResolverTest(DBTest):
    def setUp(self):
        super(CoprResolverTest, self).setUp()
        self.session.koji_mock.repoInfo.return_value = {
            'id': 123,
            'tag_name': 'f25-build',
            'state': koji.REPO_STATES['READY'],
        }
        self.request = CoprRebuildRequest(
            user_id=self.prepare_user(name='user').id,
            collection_id=self.collection.id,
            repo_source='copr:msimacek/input',
        )
        self.db.add(self.request)
        self.db.commit()
        self.resolver = CoprResolver(self.session)

    @my_vcr.use_cassette('copr_resolver1')
    @patch('koschei.backend.repo_util.get_repo', side_effect=get_repo_mock)
    @patch('koschei.backend.koji_util.get_rpm_requires_cached',
           return_value=[
               # don't rely on the order, the input is arbitrary ordered query
               ['copr-test1'],
               ['copr-test2'],  # unresolved->resolved (disappearing provide)
               ['copr-test3'],
               ['copr-test4'],  # resolved->unresolved (appearing provide)
           ])
    @patch('koschei.backend.koji_util.get_build_group_cached', return_value=['R'])
    def test_resolver(self, _, __, ___):
        packages = self.prepare_packages('c1', 'c2', 'c3', 'c4')
        for p in packages:
            p.last_complete_build_state = Build.COMPLETE
        self.db.commit()
        self.resolver.main()
        self.assertEqual(123, self.request.repo_id)
        self.assertEqual(REPO_URL, self.request.yum_repo)

        for c in self.request.resolution_changes:
            print('{} {}>{} {}'.format(c.package.name,
                                       c.prev_resolved,
                                       c.curr_resolved,
                                       c.problems))

        self.assertItemsEqual(
            # [(True, False), (False, True)], # needs repo overriding
            [(False, True)],
            [(c.prev_resolved, c.curr_resolved)
             for c in self.request.resolution_changes]
        )
        self.assertEqual(2, len(self.request.rebuilds))
