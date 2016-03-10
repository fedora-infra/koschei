from mock import patch
from common import DBTest
from koschei import plugin
from koschei.models import PackageGroup, PackageGroupRelation

class FedmsgSenderTest(DBTest):
    def setUp(self):
        super(FedmsgSenderTest, self).setUp()
        plugin.load_plugins(['fedmsg_publisher'])

    def prepare_package(self):
        pkg = self.prepare_basic_data()[0]
        groups = PackageGroup(name='c'), PackageGroup(name='xml', namespace='foo')
        for group in groups:
            self.s.add(group)
            self.s.flush()
            self.s.add(PackageGroupRelation(group_id=group.id,
                                            package_id=pkg.id))
        self.s.commit()
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
                                                 'repo': 'tag',
                                                 'collection': 'foo',
                                                 'collection_name': 'Foo',
                                                 'koji_instance': 'primary',
                                                 'groups': ['c', 'foo/xml']})

    def test_same_state(self):
        package = self.prepare_package()
        with patch('fedmsg.publish') as publish:
            plugin.dispatch_event('package_state_change', package=package,
                                  prev_state='ok', new_state='ok')
            self.assertFalse(publish.called)
