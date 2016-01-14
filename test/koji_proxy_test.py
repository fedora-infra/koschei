# Copyright (C) 2014-2015  Red Hat, Inc.
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

from common import AbstractTest
from koschei import util


class Proxied(object):
    raised = False
    multicall_raise = False

    def __init__(self, *args, **kwargs):
        self.foo = 1
        self.multicall = False
        self.__mcall_list = []

    def bar(self):
        if self.multicall:
            self.__mcall_list.append(2)
        else:
            return 2

    def baz(self):
        if self.multicall:
            self.__mcall_list.append(3)
        else:
            return 3

    def raising(self):
        if not Proxied.raised:
            Proxied.raised = True
            raise RuntimeError()
        self.raised = False
        return 4

    def multiCall(self):
        if not self.multicall:
            # raising subclass of Exception would make it stuck in infinite loop
            raise SystemExit("multicall not set")
        if Proxied.multicall_raise:
            Proxied.multicall_raise = False
            raise RuntimeError()
        res = self.__mcall_list
        self.__mcall_list = []
        self.multicall = False
        return res

koji.ClientSession = Proxied

class KojiSessionProxyTest(AbstractTest):
    def test_call(self):
        proxy = util.KojiSession()
        self.assertEquals(1, proxy.foo)
        self.assertEquals(2, proxy.bar())

    def test_raise(self):
        proxy = util.KojiSession()
        self.assertEquals(4, proxy.raising())

    def test_multicall(self):
        proxy = util.KojiSession()
        proxy.multicall = True
        proxy.bar()
        proxy.baz()
        self.assertEquals([2, 3], proxy.multiCall())

    def test_multicall_raise(self):
        proxy = util.KojiSession()
        proxy.multicall = True
        Proxied.multicall_raise = True
        proxy.bar()
        proxy.baz()
        self.assertEquals([2, 3], proxy.multiCall())
        self.assertFalse(Proxied.multicall_raise)
