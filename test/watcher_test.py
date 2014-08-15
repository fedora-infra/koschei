from mock import Mock
from common import AbstractTest

from koschei import models as m, watcher, backend

test_topic = 'org.fedoraproject.test.buildsys'
test_task_id = 666

def generate_state_change(instance='primary', task_id=test_task_id, old='OPEN', new='CLOSED'):
    return {'msg':
        {'instance': instance,
         'attribute': 'state',
         'id': task_id,
         'old': old,
         'new': new,
         }}

class WatcherTest(AbstractTest):
    def prepare_data(self):
        pkg = m.Package(name='rnv')
        self.s.add(pkg)
        self.s.flush()
        self.s.add(m.Build(package_id=pkg.id, state=m.Build.RUNNING,
                   task_id=test_task_id))
        self.s.commit()

    def test_ignored_topic(self):
        self.fedmsg.mock_add_message(topic='org.fedoraproject.prod.buildsys.task.state.change',
                                     msg=generate_state_change())
        watcher.main(None, None)

    def test_ignored_instance(self):
        self.fedmsg.mock_add_message(topic=test_topic,
                                     msg=generate_state_change(instance='ppc'))
        watcher.main(None, None)

    def test_task_completed(self):
        self.prepare_data()
        self.fedmsg.mock_add_message(topic=test_topic + '.task.state.change',
                                     msg=generate_state_change())
        watcher.main(self.s, None)
        build = self.s.query(m.Build).filter_by(task_id=test_task_id).one()
        self.assertEquals('complete', build.state_string)
