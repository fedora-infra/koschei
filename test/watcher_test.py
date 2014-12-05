import koji

from datetime import datetime
from mock import Mock, patch
from common import DBTest

from koschei import models as m
from koschei.watcher import Watcher

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
        self.fedmsg.mock_add_message(topic='org.fedoraproject.prod.buildsys.task.state.change',
                                     msg=generate_state_change())
        Watcher(db=Mock(), koji_session=Mock()).main()

    def test_ignored_instance(self):
        self.fedmsg.mock_add_message(topic=test_topic,
                                     msg=generate_state_change(instance='ppc'))
        Watcher(db=Mock(), koji_session=Mock()).main()

    def test_task_completed(self):
        _, build = self.prepare_basic_data()
        self.fedmsg.mock_add_message(topic=test_topic + '.task.state.change',
                                     msg=generate_state_change())
        backend_mock = Mock()
        watcher = Watcher(db=self.s, koji_session=Mock(), backend=backend_mock)
        watcher.main()
        backend_mock.update_build_state.assert_called_once_with(build, 'CLOSED')
