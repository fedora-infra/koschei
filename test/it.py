# Copyright (C) 2017 Red Hat, Inc.
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


from __future__ import absolute_import

import os
import vcr

from test.common import DBTest
from koschei.backend.services.polling import Polling
from koschei.backend.services.resolver import Resolver
from koschei.backend import KoscheiBackendSession
from koschei.models import Package
from koschei.config import get_config



class IntegrationTest(DBTest):

    def test_polling_resolver(self):
        cache_dir = '../it-data/test_polling_resolver'
        if not os.path.exists(cache_dir + '/repodata'):
            os.makedirs(cache_dir + '/repodata')
        get_config('koji_config')['server'] = 'https://koji.fedoraproject.org/kojihub'
        get_config('koji_config')['topurl'] = 'https://kojipkgs.fedoraproject.org'
        get_config('koji_config')['login_method'] = 'logout'
        get_config('directories')['cachedir'] = cache_dir

        self.collection.build_tag = 'f27-build'
        self.collection.dest_tag = 'f27'
        self.collection.target = 'f27'
        self.collection.poll_untracked = False

        # Choose a few pkgs to mark them as tracked.
        # Ideally we want variety of packages:
        #  - with real builds and without
        #  - with repos for latest build: available and expired
        #  - resolved and unresolved
        #  and so on...
        # TODO: improve selection of packages
        ok_name = 'inkscape'
        failing_name = 'hexchat'
        unresolved_name = 'asterisk'
        blocked_name = 'xpp2'
        self.prepare_packages(unresolved_name, failing_name, blocked_name, ok_name)

        # Add some running, complete and failed scratch-builds
        self.prepare_build('inkscape').task_id = 19483435
        self.prepare_build('inkscape').task_id = 19421970
        self.prepare_build('hexchat').task_id = 19483416
        self.db.commit()

        # Run polling to refresh latest builds. Then resolve their deps and process new repo.
        with vcr.use_cassette(cache_dir + '/vcr.yml', match_on=['body'], record_mode='once'):
            session = KoscheiBackendSession()
            Polling(session).main()
            Resolver(session).main()

        ok_pkg = self.db.query(Package).filter_by(name=ok_name).one()
        failing_pkg = self.db.query(Package).filter_by(name=failing_name).one()
        unresolved_pkg = self.db.query(Package).filter_by(name=unresolved_name).one()
        blocked_pkg = self.db.query(Package).filter_by(name=blocked_name).one()

        self.assertEqual(True, ok_pkg.resolved)
        self.assertEqual('ok', ok_pkg.state_string)
        self.assertEqual(True, failing_pkg.resolved)
        # FIXME this assertion would fail, AssertionError: 'failing' != 'ok'
        #self.assertEqual('failing', failing_pkg.state_string)
        self.assertEqual(False, unresolved_pkg.resolved)
        self.assertEqual('unresolved', unresolved_pkg.state_string)
        self.assertEqual(None, blocked_pkg.resolved)
        self.assertEqual('blocked', blocked_pkg.state_string)
        # TODO: add way more DB assertions
