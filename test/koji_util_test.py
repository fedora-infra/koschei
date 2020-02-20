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

import koji

from test.common import AbstractTest, my_vcr, with_koji_cassette
from koschei.backend import koji_util


class KojiUtilLoadTest(AbstractTest):
    def setUp(self):
        self.session = koji.ClientSession('https://koji.stg.fedoraproject.org/kojihub')

    all_arches = ['i386', 'x86_64', 'armhfp']

    @my_vcr.use_cassette('koji_load_all')
    def test_koji_load_all(self):
        load = koji_util.get_koji_load(self.session, self.all_arches, self.all_arches)
        self.assertAlmostEqual(0.8958, load, 4)

    @my_vcr.use_cassette('koji_load_noarch')
    def test_koji_load_noarch(self):
        load = koji_util.get_koji_load(self.session, self.all_arches, ['noarch'])
        self.assertAlmostEqual(0.4305, load, 4)

    @my_vcr.use_cassette('koji_load_intel')
    def test_koji_load_intel(self):
        load = koji_util.get_koji_load(self.session, self.all_arches, ['i686', 'x86_64'])
        self.assertAlmostEqual(0.5119, load, 4)

    @my_vcr.use_cassette('koji_load_arm')
    def test_koji_load_arm(self):
        load = koji_util.get_koji_load(self.session, self.all_arches, ['armv7hl'])
        self.assertAlmostEqual(0.8958, load, 4)


class KojiArchesTest(AbstractTest):
    def setUp(self):
        self.session = koji.ClientSession('https://koji.fedoraproject.org/kojihub')

    all_arches = ['aarch64', 'armv7hl', 'i686', 'ppc64', 'ppc64le', 's390x', 'x86_64']
    # allowed by koschei config = ['i386', 'x86_64', 'armhfp']

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'i686', 'x86_64', 'armv7hl'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra),
        )

    @my_vcr.use_cassette('get_srpm_arches_noarch')
    def test_get_srpm_arches_noarch(self):
        nvra = {'arch': 'src', 'name': 'maven', 'release': '9.fc26',
                'version': '3.3.9'}
        self.assertEqual(
            {'noarch'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra),
        )

    @my_vcr.use_cassette('get_srpm_arches_exclusive')
    def test_get_srpm_arches_exclusive(self):
        nvra = {'name': 'java-1.8.0-openjdk-aarch32', 'version': '1.8.0.131',
                'release': '2.170420.fc26', 'epoch': 1, 'arch': 'src'}
        self.assertEqual(
            {'armv7hl'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra),
        )

    @my_vcr.use_cassette('get_srpm_arches_exclude')
    def test_get_srpm_arches_exclude(self):
        nvra = {'name': 'ghdl', 'version': '0.34dev',
                'release': '0.20170221git663ebfd.0.fc25', 'arch': 'src'}
        self.assertEqual(
            {'i686', 'x86_64'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra),
        )

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches_override(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'i386', 'x86_64'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra,
                                      arch_override="x86_64 i386"),
        )

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches_override_non_canon(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'i686'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra,
                                      arch_override="i686"),
        )

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches_override_inv(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'i686', 'x86_64'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra,
                                      arch_override="^armhfp"),
        )

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches_override_inv_non_canon(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'i686', 'x86_64'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra,
                                      arch_override="^armv7hl"),
        )

    @my_vcr.use_cassette('get_srpm_arches')
    def test_get_srpm_arches_non_canon_build_arches(self):
        nvra = {'arch': 'src', 'name': 'rnv', 'release': '11.fc26',
                'version': '1.7.11'}
        self.assertEqual(
            {'aarch64', 'armv7hl', 'i686', 'ppc64', 'ppc64le', 's390x', 'x86_64'},
            koji_util.get_srpm_arches(self.session, self.all_arches, nvra,
                                      build_arches=self.all_arches),
        )


class KojiUtilOtherTest(AbstractTest):
    @with_koji_cassette
    def test_get_build_group(self):
        koji_session = self.koji('primary')
        group = koji_util.get_build_group(koji_session, 'f29-build', 'build', 888027)
        expected_group = [
            'tar', 'xz', 'sed', 'findutils', 'gcc', 'redhat-rpm-config',
            'make', 'shadow-utils', 'coreutils', 'which', 'gcc-c++', 'unzip',
            'fedora-release', 'bzip2', 'gawk', 'cpio', 'util-linux', 'bash',
            'info', 'grep', 'rpm-build', 'patch', 'diffutils', 'gzip',
        ]
        self.assertCountEqual(expected_group, group)

    @with_koji_cassette
    def test_create_repo_descriptor(self):
        desc = koji_util.create_repo_descriptor(self.koji('primary'), 888027)
        self.assertEqual('primary', desc.koji_id)
        self.assertEqual('f29-build', desc.build_tag)
        self.assertEqual(888027, desc.repo_id)
        self.assertEqual(
            'https://primary-koji.test/repos/f29-build/888027/x86_64',
            desc.url,
        )

    @with_koji_cassette
    def test_koji_fault(self):
        koji_sesion = self.koji('primary')
        # Successful task
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 1337))
        # Cancelled task
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 12345))
        # Failed buildArch task (non-fault)
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 41684693))
        # Failed build task (non-fault)
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 41684668))
        # Failed rebuildSRPM (non-fault)
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 41111818))
        # Failed scratch build from SRPM (non-fault)
        self.assertFalse(koji_util.is_koji_fault(koji_sesion, 41111817))
        # Failed build task due to HTTPError: HTTP Error 503: Backend fetch failed
        self.assertTrue(koji_util.is_koji_fault(koji_sesion, 32738401))
        # Failed buildArch task due to HTTPError: HTTP Error 503: Backend fetch failed
        self.assertTrue(koji_util.is_koji_fault(koji_sesion, 32738626))
