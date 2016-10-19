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

import koji
from mock import patch, call

from test.common import DBTest
from koschei.backend.services.polling import Polling


class PollingTest(DBTest):
    def test_poll_none(self):
        self.prepare_build('rnv', True)
        self.prepare_build('eclipse', False)
        with patch('koschei.backend.update_build_state') as update_mock:
            polling = Polling(self.session)
            polling.poll_builds()
            self.assertFalse(self.session.koji_mock.getTaskInfo.called)
            self.assertFalse(update_mock.called)

    def test_poll_complete(self):
        build = self.prepare_build('rnv')
        self.session.koji_mock.getTaskInfo.return_value = \
            {'state': koji.TASK_STATES['CLOSED']}
        with patch('koschei.backend.update_build_state') as update_mock:
            polling = Polling(self.session)
            polling.poll_builds()
            update_mock.assert_called_once_with(self.session, build, 'CLOSED')

    def test_poll_multiple(self):
        rnv_build = self.prepare_build('rnv')
        eclipse_build = self.prepare_build('eclipse')
        self.prepare_build('expat', False)
        self.session.koji_mock.getTaskInfo.return_value = \
            {'state': koji.TASK_STATES['FAILED']}
        with patch('koschei.backend.update_build_state') as update_mock:
            polling = Polling(self.session)
            polling.poll_builds()
            update_mock.assert_has_calls(
                [call(self.session, rnv_build, 'FAILED'),
                 call(self.session, eclipse_build, 'FAILED')],
                any_order=True,
            )
