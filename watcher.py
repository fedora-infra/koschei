import fedmsg
import fedmsg.consumers
import logging

from models import Build, Session, Package
from submitter import update_koji_state
from plugins import dispatch_event, load_plugins

log = logging.getLogger('koschei-watcher')

load_plugins()

class KojiWatcher(fedmsg.consumers.FedmsgConsumer):
    topic = 'org.fedoraproject.prod.buildsys.*'
    config_key = 'koschei.koji-watcher'

    def consume(self, msg):
        topic = msg['topic']
        content = msg['body']['msg']
        if topic == 'org.fedoraproject.prod.buildsys.task.state.change':
            update_build_state(content)
        elif topic == 'org.fedoraproject.prod.buildsys.repo.done':
            if content.get('tag') == 'f21-build':
                session = Session()
                dispatch_event('repo_done', session)
                session.close()
        elif topic == 'org.fedoraproject.prod.buildsys.tag':
            if content.get('instance') == 'primary' and content.get('tag') == 'f21':
                session = Session()
                pkg = session.query(Package).filter_by(name=content['name']).first()
                if pkg:
                    print('Calling build tagged for {}'.format(pkg.name))
                    dispatch_event('build_tagged', session, pkg,
                                   content['version'], content['release'])
                session.close()

def update_build_state(msg):
    assert msg['attribute'] == 'state'
    session = Session()
    task_id = msg['id']
    build = session.query(Build).filter_by(task_id=task_id).first()
    if build:
        state = msg['new']
        update_koji_state(session, build, state)
    session.close()
