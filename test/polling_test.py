# Copyright (C) 2014-2020  Red Hat, Inc.
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

from mock import patch, call

from test.common import DBTest, with_koji_cassette
from koschei.models import Build
from koschei.backend.services.polling import Polling


class PollingTest(DBTest):
    @with_koji_cassette
    def test_poll_none(self):
        self.prepare_build('rnv', 'complete')
        self.prepare_build('eclipse', 'failed')
        with patch('koschei.backend.update_build_state') as update_mock:
            polling = Polling(self.session)
            polling.poll_builds()
            self.assertFalse(update_mock.called)

    @with_koji_cassette
    def test_poll_multiple(self):
        rnv_build = self.prepare_build('rnv', 'running', task_id=26033406)
        eclipse_build = self.prepare_build('eclipse', 'running', task_id=26151873)
        self.prepare_build('maven', 'complete', task_id=26035462)
        with patch('koschei.backend.update_build_state') as update_mock:
            polling = Polling(self.session)
            polling.poll_builds()
            update_mock.assert_has_calls(
                [call(self.session, rnv_build, 'CLOSED'),
                 call(self.session, eclipse_build, 'CLOSED')],
                any_order=True,
            )

    @with_koji_cassette
    def test_poll_failed_rebuildSRPM(self):
        build = self.prepare_build('python-debtcollector', 'running', task_id=41111817)
        with patch('koschei.backend.dispatch_event') as event:
            Polling(self.session).poll_builds()
            event.assert_called_once()
            event.assert_called_once_with(
                'package_state_change',
                session=self.session,
                package=build.package,
                prev_state='ignored',
                new_state='failing',
            )
        build = self.db.query(Build).one()
        self.assertEqual(build.state, Build.FAILED)
        self.assertEqual(build.repo_id, 1344909)
