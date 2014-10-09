from mock import patch
from common import DBTest
from koschei import plugin
from koschei.models import PackageGroup, PackageGroupRelation
from koschei.backend import PackageStateUpdateEvent

class FedmsgSenderTest(DBTest):
    def setUp(self):
        super(FedmsgSenderTest, self).setUp()
        plugin.load_plugins(['fedmsg_publisher'])

    def prepare_package(self):
        pkg = self.prepare_basic_data()[0]
        groups = PackageGroup(name='c'), PackageGroup(name='xml')
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
            PackageStateUpdateEvent(package, 'failed', 'ok').dispatch()
            publish.assert_called_once_with(topic='package.state.change',
                                            modname='koschei',
                                            msg={'name': 'rnv',
                                                 'old': 'failed',
                                                 'new': 'ok',
                                                 'repo': 'f22',
                                                 'koji_instance': 'primary',
                                                 'groups': ['c', 'xml']})

    def test_same_state(self):
        package = self.prepare_package()
        with patch('fedmsg.publish') as publish:
            PackageStateUpdateEvent(package, 'ok', 'ok').dispatch()
            self.assertFalse(publish.called)
