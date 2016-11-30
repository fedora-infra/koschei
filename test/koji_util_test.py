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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from test.common import AbstractTest, RecordedKojiSession, my_vcr
from koschei.backend import koji_util

class KojiUtilTest(AbstractTest):
    def setUp(self):
        self.session = RecordedKojiSession('https://koji.stg.fedoraproject.org/kojihub')

    @my_vcr.use_cassette('koji_load_all')
    def test_koji_load_all(self):
        load = koji_util.get_koji_load(self.session, [])
        self.assertAlmostEqual(0.8958, load, 4)

    @my_vcr.use_cassette('koji_load_noarch')
    def test_koji_load_noarch(self):
        load = koji_util.get_koji_load(self.session, ['noarch'])
        self.assertAlmostEqual(0.4305, load, 4)

    @my_vcr.use_cassette('koji_load_intel')
    def test_koji_load_intel(self):
        load = koji_util.get_koji_load(self.session, ['i386', 'x86_64'])
        self.assertAlmostEqual(0.5119, load, 4)

    @my_vcr.use_cassette('koji_load_arm')
    def test_koji_load_arm(self):
        load = koji_util.get_koji_load(self.session, ['aarch64', 'armhfp'])
        self.assertAlmostEqual(0.8958, load, 4)
