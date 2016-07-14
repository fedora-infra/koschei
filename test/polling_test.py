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
from mock import Mock, call

from test.common import DBTest
from koschei.backend.services.polling import Polling


class PollingTest(DBTest):

    def get_koji_mock(self, state='CLOSED'):
        koji_mock = Mock()

        def multiCall():
            return [[{'state': koji.TASK_STATES[state]}]] * 2
        koji_mock.multiCall = multiCall
        return koji_mock

    def test_poll_none(self):
        self.prepare_build('rnv', True)
        self.prepare_build('eclipse', False)
        koji_mock = self.get_koji_mock()
        backend_mock = Mock()
        polling = Polling(db=self.db, koji_sessions={'primary': koji_mock,
                                                    'secondary': koji_mock},
                          backend=backend_mock)
        polling.poll_builds()
        self.assertFalse(koji_mock.getTaskInfo.called)
        self.assertFalse(backend_mock.update_build_state.called)

    def test_poll_complete(self):
        build = self.prepare_build('rnv')
        backend_mock = Mock()
        koji_mock = self.get_koji_mock()
        polling = Polling(db=self.db, koji_sessions={'primary': koji_mock,
                                                    'secondary': koji_mock},
                          backend=backend_mock)
        polling.poll_builds()
        backend_mock.update_build_state.assert_called_once_with(build, 'CLOSED')

    def test_poll_multiple(self):
        rnv_build = self.prepare_build('rnv')
        eclipse_build = self.prepare_build('eclipse')
        self.prepare_build('expat', False)
        backend_mock = Mock()
        koji_mock = self.get_koji_mock(state='FAILED')
        polling = Polling(db=self.db, koji_sessions={'primary': koji_mock,
                                                    'secondary': koji_mock},
                          backend=backend_mock)
        polling.poll_builds()
        backend_mock.update_build_state.assert_has_calls(
            [call(rnv_build, 'FAILED'),
             call(eclipse_build, 'FAILED')],
            any_order=True)
