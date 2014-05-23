from __future__ import print_function

import logging

from util import create_koji_session, koji_scratch_build
from models import Session, Package, Build

log = logging.getLogger('submitter')

def submit_builds():
    koji_session = create_koji_session()
    db_session = Session()
    scheduled_builds = db_session.query(Build).filter_by(state='scheduled')
    for build in scheduled_builds:
        name = build.package.name
        build.state = 'running'
        db_session.commit()
        koji_scratch_build(koji_session, name)

