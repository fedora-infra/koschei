from mock import Mock, patch
from common import AbstractTest
from koschei import plugin
from koschei.backend import PackageStateUpdateEvent

class FedmsgSenderTest(AbstractTest):
    def setUp(self):
        super(FedmsgSenderTest, self).setUp()
        plugin.load_plugins(['fedmsg_publisher'])

    def test_event(self):
        package = Mock(spec_set=['name', 'id'])
        package.name = 'rnv'
        with patch('fedmsg.publish') as publish:
            PackageStateUpdateEvent(package, 'failed', 'ok').dispatch()
            publish.assert_called_once_with(topic='package.state.change',
                                            modname='koschei',
                                            msg={'name': 'rnv',
                                                 'old': 'failed',
                                                 'new': 'ok',
                                                 'repo': 'f22',
                                                 'koji_instance': 'primary'})

    def test_same_state(self):
        package = Mock(spec_set=['name', 'id'])
        package.name = 'rnv'
        with patch('fedmsg.publish') as publish:
            PackageStateUpdateEvent(package, 'ok', 'ok').dispatch()
            self.assertFalse(publish.called)
