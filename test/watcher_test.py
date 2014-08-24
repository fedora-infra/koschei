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
        Watcher(db_session=Mock(), koji_session=Mock()).main()

    def test_ignored_instance(self):
        self.fedmsg.mock_add_message(topic=test_topic,
                                     msg=generate_state_change(instance='ppc'))
        Watcher(db_session=Mock(), koji_session=Mock()).main()

    def test_task_completed(self):
        _, build = self.prepare_basic_data()
        self.fedmsg.mock_add_message(topic=test_topic + '.task.state.change',
                                     msg=generate_state_change())
        with patch('koschei.backend.update_build_state') as mock:
            Watcher(db_session=self.s, koji_session=Mock()).main()
            mock.assert_called_once_with(self.s, build, 'CLOSED')

    def test_real_build(self):
        pkg, build = self.prepare_basic_data()
        msg = {'msg':
            {'instance': 'primary',
             'attribute': 'state',
             'build_id': 123,
             'name': 'rnv',
             'version': '1',
             'release': '2',
             'old': 0,
             'new': koji.BUILD_STATES['COMPLETE'],
             }}
        self.fedmsg.mock_add_message(topic=test_topic + '.build.state.change',
                                     msg=msg)
        koji_mock = Mock()
        koji_mock.getLatestBuilds = Mock(return_value=[{'build_id': 123,
            'name': 'rnv', 'epoch': None, 'version': '1', 'release': '2',
            'task_id': 100, 'creation_time': '2014-06-08 07:09:11.339276',
            'completion_time': '2014-06-08 07:17:41.129985', 'nvr': 'rnv-1-2',
            'state': koji.BUILD_STATES['COMPLETE']}])
        with patch('koschei.backend.build_registered') as mock:
            Watcher(db_session=self.s, koji_session=koji_mock).main()
            koji_mock.getLatestBuilds.assert_called_once_with('f22', package='rnv')
            new_build = self.s.query(m.Build).filter(m.Build.id != build.id).one()
            mock.assert_called_once_with(self.s, new_build)
            self.assertTrue(new_build.real)
            self.assertEqual(pkg.id, new_build.package_id)
            self.assertTrue(new_build.epoch is None)
            self.assertEqual('1', new_build.version)
            self.assertEqual('2', new_build.release)
            self.assertEqual(100, new_build.task_id)
            self.assertEqual(datetime(2014, 6, 8, 7, 9, 11, 339276), new_build.started)
            #self.assertEqual(datetime(2014, 6, 8, 7, 17, 41, 129985), new_build.finished)
            self.assertEqual(m.Build.COMPLETE, new_build.state)
