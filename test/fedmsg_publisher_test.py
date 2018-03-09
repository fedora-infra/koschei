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
        plugin.load_plugins('backend', ['fedmsg'])

    def test_event(self):
        package = self.prepare_package('rnv')
        self.prepare_group('c', content=['rnv'])
        self.prepare_group('xml', namespace='foo', content=['rnv'])
        with patch('fedmsg.publish') as publish:
            plugin.dispatch_event('package_state_change', self.session, package=package,
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
            publish.reset_mock()
            plugin.dispatch_event('collection_state_change', self.session, collection=package.collection,
                                  prev_state='unresolved', new_state='ok')
            publish.assert_called_once_with(topic='collection.state.change',
                                            modname='koschei',
                                            msg={'old': 'unresolved',
                                                 'new': 'ok',
                                                 'collection': 'f25',
                                                 'collection_name': 'Fedora Rawhide',
                                                 'repo_id': 123,
                                                 'koji_instance': 'primary'})


    def test_same_state(self):
        package = self.prepare_package('rnv')
        self.prepare_group('c', content=['rnv'])
        self.prepare_group('xml', namespace='foo', content=['rnv'])
        with patch('fedmsg.publish') as publish:
            plugin.dispatch_event('package_state_change', self.session, package=package,
                                  prev_state='ok', new_state='ok')
            self.assertFalse(publish.called)
            publish.reset_mock()
            plugin.dispatch_event('collection_state_change', self.session, collection=package.collection,
                                  prev_state='ok', new_state='ok')
            self.assertFalse(publish.called)
