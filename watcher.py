import fedmsg
import fedmsg.consumers
import logging

from models import Build, Session

log = logging.getLogger('fedora-ci-watcher')

class KojiWatcher(fedmsg.consumers.FedmsgConsumer):
    topic = 'org.fedoraproject.prod.buildsys.*'
    config_key = 'fedora-ci.koji-watcher'

    def consume(self, msg):
        topic = msg['topic']
        content = msg['body']['msg']
        if topic == u'org.fedoraproject.prod.buildsys.task.state.change':
            update_build_state(content)


def update_build_state(msg):
    assert msg['attribute'] == 'state'
    session = Session()
    task_id = msg['id']
    build = session.query(Build).filter_by(task_id=task_id).first()
    if build:
        state = msg['new']
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            log.info('fedmsg: Setting build {build} state to {state}'\
                      .format(build=build, state=Build.REV_STATE_MAP[state]))
            build.state = state
            session.commit()
    session.close()


