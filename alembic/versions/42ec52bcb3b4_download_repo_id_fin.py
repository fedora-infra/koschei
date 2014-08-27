"""Download repo_id, finished time and subtasks from Koji

Revision ID: 42ec52bcb3b4
Revises: 258ebf8f2f2a
Create Date: 2014-08-27 22:04:17.383227

"""

# revision identifiers, used by Alembic.
revision = '42ec52bcb3b4'
down_revision = '258ebf8f2f2a'

from alembic import op

from koschei.models import Build, Session, KojiTask
from koschei.util import create_koji_session, parse_koji_time

def upgrade():
    s = Session(bind=op.get_bind())
    k = create_koji_session(anonymous=True)

    step = 100

    off = 0
    proc = 0
    while True:
        k.multicall = True
        builds = s.query(Build).order_by(Build.id)[off:off + step]
        if not builds:
            break
        for build in builds:
            k.getTaskInfo(build.task_id) # for finished
            k.getTaskChildren(build.task_id, request=True) #for repo_id and koji_tasks
        result = k.multiCall()
        assert len(result) == len(builds) * 2
        for build, [build_task], [subtasks] in zip(builds, result[::2], result[1::2]):
            if build_task['completion_time']:
                build.finished = parse_koji_time(build_task['completion_time'])
            build_arch_tasks = [task for task in subtasks if task['method'] == 'buildArch']
            for task in build_arch_tasks:
                try:
                    build.repo_id = task['request'][4]['repo_id']
                except KeyError:
                    pass
                if not s.query(KojiTask).filter_by(task_id=task['id']).count():
                    db_task = KojiTask(build_id=build.id, task_id=task['id'],
                                       state=task['state'], started=task['create_time'],
                                       finished=task['completion_time'], arch=task['arch'])
                    s.add(db_task)
        proc += len(builds)
        s.flush()
        off += step
        print "processed {} builds".format(proc)

def downgrade():
    pass
