# Copyright (C) 2014-2016 Red Hat, Inc.
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

from mock import patch
from test.common import DBTest
from koschei import plugin
from koschei.models import PackageGroup, PackageGroupRelation

class FedmsgSenderTest(DBTest):
    def setUp(self):
        super(FedmsgSenderTest, self).setUp()
        plugin.load_plugins('backend', ['fedmsg_publisher'])

    def prepare_package(self):
        pkg = self.prepare_basic_data()[0]
        groups = PackageGroup(name='c'), PackageGroup(name='xml', namespace='foo')
        for group in groups:
            self.db.add(group)
            self.db.flush()
            self.db.add(PackageGroupRelation(group_id=group.id,
                                            base_id=pkg.base_id))
        self.db.commit()
        return pkg

    def test_event(self):
        package = self.prepare_package()
        with patch('fedmsg.publish') as publish:
            plugin.dispatch_event('package_state_change', package=package,
                                  prev_state='failed', new_state='ok')
            publish.assert_called_once_with(topic='package.state.change',
                                            modname='koschei',
                                            msg={'name': 'rnv',
                                                 'old': 'failed',
                                                 'new': 'ok',
                                                 'repo': 'f25',
                                                 'collection': 'f25',
                                                 'collection_name': 'Fedora Rawhide',
                                                 'koji_instance': 'primary',
                                                 'groups': ['c', 'foo/xml']})

    def test_same_state(self):
        package = self.prepare_package()
        with patch('fedmsg.publish') as publish:
            plugin.dispatch_event('package_state_change', package=package,
                                  prev_state='ok', new_state='ok')
            self.assertFalse(publish.called)
