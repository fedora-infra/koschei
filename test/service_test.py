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

import koji
import socket

from mock import Mock, patch, call
from common import AbstractTest
from koschei.service import Service, KojiService

class MyException(Exception):
    pass

class MyOtherException(Exception):
    pass

class MyService(Service):
    retry_on = (MyOtherException,)

    def __init__(self, main=None, on_except=None, *args, **kwargs):
        self.__class__.main = main or (lambda inst: 0)
        self.__class__.on_exception = on_except
        super(MyService, self).__init__(*args, **kwargs)

class ServiceTest(AbstractTest):
    def test_abstract(self):
        s = Service(log=Mock(), db=Mock())
        self.assertRaises(NotImplementedError, s.main)

    def test_create_session(self):
        with patch('koschei.service.Session') as create:
            s = MyService(log=Mock())
            create.assert_called_once_with()
            self.assertIs(create(), s.db)

    def test_create_log(self):
        with patch('logging.getLogger') as create:
            s = MyService(db=Mock())
            create.assert_called_once_with('koschei.myservice')
            self.assertIs(create(), s.log)

    def test_run(self):
        with patch('time.sleep') as sleep:
            called = [0]
            def main(inst):
                called[0] += 1
                if called[0] == 3:
                    raise MyException()
            mock_log = Mock()
            mock_db = Mock()
            s = MyService(main, log=mock_log, db=mock_db)
            self.assertRaises(MyException, s.run_service)
            self.assertEqual(3, called[0])
            self.assertEqual(3, mock_db.rollback.call_count)
            mock_log.info.assert_called()
            sleep.assert_has_calls([call(3)] * 2)

    def test_interrupt(self):
        def main(inst):
            raise KeyboardInterrupt()
        mock_log = Mock()
        mock_db = Mock()
        s = MyService(main, log=mock_log, db=mock_db)
        self.assertRaises(SystemExit, s.run_service)

    def test_retry(self):
        with patch('time.sleep') as sleep:
            called = [0] * 2
            def main(inst):
                called[0] += 1
                if 3 <= called[0] < 5:
                    raise MyOtherException()
                elif called[0] == 5:
                    raise MyException()
            def on_except(inst, exc):
                called[1] += 1
                self.assertIsInstance(exc, MyOtherException)
            mock_log = Mock()
            mock_db = Mock()
            s = MyService(main, on_except=on_except, log=mock_log, db=mock_db)
            self.assertRaises(MyException, s.run_service)
            self.assertEqual(5, called[0])
            self.assertEqual(2, called[1])
            self.assertEqual(5, mock_db.rollback.call_count)
            mock_log.error.assert_called()
            sleep.assert_has_calls([call(3)] * 2 +
                                   [call(10), call(20)])

    def test_find_nonexistent(self):
        svc = Service.find_service('nonexistent')
        self.assertIsNone(svc)

    def test_find_myservice(self):
        svc = Service.find_service('myservice')
        self.assertIs(MyService, svc)

class KojiServiceTest(AbstractTest):
    def test_proxy(self):
        session_mock = Mock()
        with patch('koschei.util.Proxy') as proxy:
            s = KojiService(log=Mock(), db=Mock(),
                            koji_session=session_mock)
            proxy.assert_called_with(session_mock)
            self.assertEqual(proxy(), s.koji_session)

    def test_args(self):
        with patch('koschei.service.Service.__init__') as init:
            mock_log = Mock()
            mock_db = Mock()
            mock_koji = Mock()
            KojiService(log=mock_log, db=mock_db, koji_session=mock_koji)
            init.assert_called_once_with(log=mock_log, db=mock_db)

    def test_anon(self):
        with patch('koschei.util.create_koji_session') as create:
            KojiService(log=Mock(), db=Mock())
            create.assert_called_with(anonymous=True)

    def test_not_anon(self):
        with patch('koschei.util.create_koji_session') as create:
            class MyKojiSvc(KojiService):
                koji_anonymous = False
            MyKojiSvc(log=Mock(), db=Mock())
            create.assert_called_with(anonymous=False)
