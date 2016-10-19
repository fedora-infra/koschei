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

from mock import Mock, patch, call

from test.common import AbstractTest
from koschei.backend.service import Service


class MyException(Exception):
    pass


class MyService(Service):
    def __init__(self, main=None, *args, **kwargs):
        self.__class__.main = main or (lambda inst: 0)
        super(MyService, self).__init__(*args, **kwargs)


class ServiceTest(AbstractTest):
    def test_abstract(self):
        s = Service(session=Mock())
        self.assertRaises(NotImplementedError, s.main)

    def test_run(self):
        with patch('time.sleep') as sleep:
            called = [0]

            def main(inst):
                called[0] += 1
                if called[0] == 3:
                    raise MyException()
            s = MyService(main, session=Mock())
            self.assertRaises(MyException, s.run_service)
            self.assertEqual(3, called[0])
            sleep.assert_has_calls([call(3)] * 2)

    def test_find_nonexistent(self):
        svc = Service.find_service('nonexistent')
        self.assertIsNone(svc)

    def test_find_myservice(self):
        svc = Service.find_service('myservice')
        self.assertIs(MyService, svc)
