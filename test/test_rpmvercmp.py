# Copyright (C) 2016 Red Hat, Inc.
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

from sqlalchemy import literal_column

from test.common import DBTest
from koschei.db import RpmEVR
from koschei.models import UnappliedChange

# run the following in RPM's source tree to regenerate the test data
# ruby -nle 'if /^RPMVERCMP\(([^,]+),\s*([^,]+),\s*([^,]+)\)$/ then print "(\x27#{$1}\x27, \x27#{$2}\x27, #{$3})," end' tests/rpmvercmp.at
RPM_DATA = """
('1.0', '1.0', 0),
('1.0', '2.0', -1),
('2.0', '1.0', 1),
('2.0.1', '2.0.1', 0),
('2.0', '2.0.1', -1),
('2.0.1', '2.0', 1),
('2.0.1a', '2.0.1a', 0),
('2.0.1a', '2.0.1', 1),
('2.0.1', '2.0.1a', -1),
('5.5p1', '5.5p1', 0),
('5.5p1', '5.5p2', -1),
('5.5p2', '5.5p1', 1),
('5.5p10', '5.5p10', 0),
('5.5p1', '5.5p10', -1),
('5.5p10', '5.5p1', 1),
('10xyz', '10.1xyz', -1),
('10.1xyz', '10xyz', 1),
('xyz10', 'xyz10', 0),
('xyz10', 'xyz10.1', -1),
('xyz10.1', 'xyz10', 1),
('xyz.4', 'xyz.4', 0),
('xyz.4', '8', -1),
('8', 'xyz.4', 1),
('xyz.4', '2', -1),
('2', 'xyz.4', 1),
('5.5p2', '5.6p1', -1),
('5.6p1', '5.5p2', 1),
('5.6p1', '6.5p1', -1),
('6.5p1', '5.6p1', 1),
('6.0.rc1', '6.0', 1),
('6.0', '6.0.rc1', -1),
('10b2', '10a1', 1),
('10a2', '10b2', -1),
('1.0aa', '1.0aa', 0),
('1.0a', '1.0aa', -1),
('1.0aa', '1.0a', 1),
('10.0001', '10.0001', 0),
('10.0001', '10.1', 0),
('10.1', '10.0001', 0),
('10.0001', '10.0039', -1),
('10.0039', '10.0001', 1),
('4.999.9', '5.0', -1),
('5.0', '4.999.9', 1),
('20101121', '20101121', 0),
('20101121', '20101122', -1),
('20101122', '20101121', 1),
('2_0', '2_0', 0),
('2.0', '2_0', 0),
('2_0', '2.0', 0),
('a', 'a', 0),
('a+', 'a+', 0),
('a+', 'a_', 0),
('a_', 'a+', 0),
('+a', '+a', 0),
('+a', '_a', 0),
('_a', '+a', 0),
('+_', '+_', 0),
('_+', '+_', 0),
('_+', '_+', 0),
('+', '_', 0),
('_', '+', 0),
('1.0~rc1', '1.0~rc1', 0),
('1.0~rc1', '1.0', -1),
('1.0', '1.0~rc1', 1),
('1.0~rc1', '1.0~rc2', -1),
('1.0~rc2', '1.0~rc1', 1),
('1.0~rc1~git123', '1.0~rc1~git123', 0),
('1.0~rc1~git123', '1.0~rc1', -1),
('1.0~rc1', '1.0~rc1~git123', 1),
"""


class RpmVercmpTest(DBTest):
    def test_rpmvercmp(self):
        result = self.db.execute("""
            SELECT a, b, exp, rpmvercmp(a, b) AS act FROM
                (VALUES {values}) AS q(a, b, exp)
            WHERE exp != rpmvercmp(a, b)
        """.format(values=RPM_DATA.rstrip(',\n '))).fetchall()
        self.assertCountEqual([], result)

    def test_rpmvercmp_evr(self):
        expr = "rpmvercmp_evr(0, '1.1', '11.fc26', NULL, '1.1', '8.fc26')"
        result = self.db.query(literal_column(expr).label('r')).scalar()
        self.assertEqual(1, result)

    def test_rpmevr_comparison(self):
        evr1 = RpmEVR(0, '1.1', '11.fc26')
        evr2 = RpmEVR(None, '1.1', '8.fc26')
        self.assertGreater(evr1, evr2)

    def test_rpmevr_db_comparison(self):
        build = self.prepare_build('rnv')
        change = UnappliedChange(
            package_id=build.package.id, dep_name='foo',
            prev_epoch=None, prev_version='1.1', prev_release='8.fc26',
            curr_epoch=None, curr_version='1.1', curr_release='8.fc26',
        )
        self.db.add(change)
        evr = RpmEVR(0, '1.1', '11.fc26')
        res = self.db.query(UnappliedChange)\
            .filter(UnappliedChange.prev_evr > evr).first()
        self.assertIsNone(res)
        res = self.db.query(UnappliedChange)\
            .filter(UnappliedChange.prev_evr < evr).first()
        self.assertEqual(change, res)
