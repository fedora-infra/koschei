import time
import signal

from mock import Mock
from common import DBTest

from koschei.watcher import Watcher, WatchdogInterrupt

test_topic = 'org.fedoraproject.test.buildsys'

def generate_state_change(instance='primary', task_id=666, old='OPEN', new='CLOSED'):
    return {'msg':
        {'instance': instance,
         'attribute': 'state',
         'id': task_id,
         'old': old,
         'new': new,
         }}

class WatcherTest(DBTest):
    def test_ignored_topic(self):
        class FedmsgMock(object):
            def tail_messages(self):
                yield ('', '', 'org.fedoraproject.prod.buildsys.task.state.change',
                       generate_state_change())
        Watcher(db=Mock(), koji_session=Mock(), fedmsg_context=FedmsgMock()).main()

    def test_ignored_instance(self):
        class FedmsgMock(object):
            def tail_messages(self):
                yield ('', '', test_topic,
                       generate_state_change(instance='ppc'))
        Watcher(db=Mock(), koji_session=Mock(), fedmsg_context=FedmsgMock()).main()

    def test_task_completed(self):
        class FedmsgMock(object):
            def tail_messages(self):
                yield ('', '', test_topic + '.task.state.change',
                       generate_state_change())
        _, build = self.prepare_basic_data()
        backend_mock = Mock()
        watcher = Watcher(db=self.s, koji_session=Mock(), backend=backend_mock,
                          fedmsg_context=FedmsgMock())
        watcher.main()
        backend_mock.update_build_state.assert_called_once_with(build, 'CLOSED')

    def test_watchdog(self):
        class FedmsgMock(object):
            def tail_messages(self):
                time.sleep(5)
                assert False
        watcher = Watcher(db=Mock(), koji_session=Mock(),
                          fedmsg_context=FedmsgMock())
        try:
            self.assertRaises(WatchdogInterrupt, watcher.main)
        finally:
            signal.alarm(0)
