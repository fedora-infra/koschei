import os
import shutil

from mock import Mock, patch
from common import AbstractTest, datadir

from koschei import srpm_cache

class SrpmCacheTest(AbstractTest):
    def setUp(self):
        super(SrpmCacheTest, self).setUp()
        shutil.copytree(os.path.join(datadir, 'srpms'), 'srpms')

    def test_cached(self):
        cache = srpm_cache.SRPMCache(None)
        self.assertEqual('srpms/rnv-1.7.11-6.fc21.src.rpm',
                         cache.get_srpm('rnv', '1.7.11', '6.fc21'))
        self.assertEqual('srpms/xpp3-1.1.4-3.c.fc21.src.rpm',
                         cache.get_srpm('xpp3', '1.1.4', '3.c.fc21'))
        self.assertEqual('srpms/aether-1.0.0-3.fc21.src.rpm',
                         cache.get_srpm('aether', '1.0.0', '3.fc21'))

    def test_download(self):
        koji_mock = Mock()
        build_info = self.get_json_data('build_info_eclipse.json')
        rpm_listing = self.get_json_data('list_rpms_eclipse.json')
        koji_mock.getBuild.return_value = build_info
        koji_mock.listRPMs.return_value = rpm_listing
        cache = srpm_cache.SRPMCache(koji_mock)
        with patch('koschei.util.download_rpm_header') as dl_mock:
            cache.get_srpm('eclipse', '4.4.0', '10.fc22')
            koji_mock.getBuild.assert_called_once_with('eclipse-4.4.0-10.fc22')
            koji_mock.listRPMs.assert_called_once_with(548392, arches='src')
            dl_mock.assert_called_once_with(
                'koji.fake/packages/eclipse/4.4.0/10.fc22/src/eclipse-4.4.0-10.fc22.src.rpm',
                'srpms/eclipse-4.4.0-10.fc22.src.rpm')
