import json

from sqlalchemy import create_engine, Column, Integer, String, Boolean, \
                       ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.engine.url import URL

# TODO look for it in better place than $PWD
config_path = 'config.json'
with open(config_path) as config_file:
    config = json.load(config_file)

Base = declarative_base()

engine = create_engine(URL(**config['database_config']), echo=False)

Session = sessionmaker(bind=engine)

class Dependency(Base):
    __tablename__ = 'dependency'
    package_id = Column(Integer, ForeignKey('package.id'), primary_key=True)
    dependency_id = Column(Integer, ForeignKey('package.id'), primary_key=True)
    runtime = Column(Boolean, nullable=False, primary_key=True)

    def __repr__(self):
        return '{0.package.name} -> {0.dependency.name}'.format(self)

class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    watched = Column(Boolean, nullable=False, default=False)
    builds = relationship('Build', backref='package', lazy='dynamic')
    static_priority = Column(Integer, nullable=False, default=0)

    dependencies = relationship(Dependency, backref='package',
                                primaryjoin=(id == Dependency.package_id))
    dependants = relationship(Dependency, backref='dependency',
                                primaryjoin=(id == Dependency.dependency_id))

    priority_changes = relationship('PriorityChange', backref='package',
                                    lazy='dynamic')

    def get_finished_builds(self, since=None, until=None):
        filters = [Build.state.in_(Build.FINISHED_STATES)]
        if since:
            filters.append(Build.started >= since)
        if until:
            filters.append(Build.started < until)
        return self.builds.filter(*filters)

    def __repr__(self):
        return '{0.id} (name={0.name})'.format(self)

class Build(Base):
    __tablename__ = 'build'

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id'))
    state = Column(Integer, nullable=False, default=0)
    task_id = Column(Integer)
    logs_downloaded = Column(Boolean, default=False, nullable=False)
    triggered_by = relationship('PriorityChange', backref='applied_in',
                                lazy='dynamic')
    started = Column(DateTime)
    finished = Column(DateTime)

    STATE_MAP = {'scheduled': 0,
                 'running': 2,
                 'complete': 3,
                 'canceled': 4,
                 'failed': 5,
                }
    SCHEDULED = STATE_MAP['scheduled']
    RUNNING = STATE_MAP['running']
    COMPLETE = STATE_MAP['complete']
    CANCELED = STATE_MAP['canceled']
    FAILED = STATE_MAP['failed']
    REV_STATE_MAP = {v: k for k, v in STATE_MAP.items()}

    UNFINISHED_STATES = [SCHEDULED, RUNNING]
    FINISHED_STATES = [COMPLETE, FAILED, CANCELED]
    STATES = UNFINISHED_STATES + FINISHED_STATES

    KOJI_STATE_MAP = {'CLOSED': COMPLETE,
                      'CANCELED': CANCELED,
                      'FAILED': FAILED}

    @property
    def state_string(self):
        return self.REV_STATE_MAP[self.state]

    def __repr__(self):
        return '{0.id} (name={0.package.name}, state={self.state_string})'.format(self)

class PriorityChange(Base):
    __tablename__ = 'priority_change'

    id = Column(Integer, primary_key=True)
    plugin_name = Column(String, nullable=False)
    package_id = Column(Integer, ForeignKey('package.id'))
    value = Column(Integer, nullable=False)
    effective = Column(Boolean, nullable=False, default=True)
    applied_in_id = Column(Integer, ForeignKey('build.id'), nullable=True)
    comment = Column(String, nullable=False)

def current_priorities(session):
    return session.query(PriorityChange).filter_by(applied_in_id=None)
