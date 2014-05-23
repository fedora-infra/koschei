from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.engine.url import URL

db_settings = {'drivername': 'postgres',
               'host': 'localhost',
               'port': '5432',
               'username': 'msimacek',
               'password': 'fedorawtf',
               'database': 'fedora-ci'}

Base = declarative_base()

engine = create_engine(URL(**db_settings), echo=False)

Session = sessionmaker(bind=engine)


class Package(Base):
    __tablename__ = 'package'

    id = Column(Integer, primary_key=True)
    name = Column('name', String)
    priority = Column('priority', Integer)
    builds = relationship('Build', backref='package')

    def __repr__(self):
        return '{0.id} (name={0.name}, prio={0.prio})'.format(self)

class Build(Base):
    __tablename__ = 'build'

    UNFINISHED_STATES = ['scheduled', 'running']
    FINISHED_STATES = ['complete', 'failed', 'cancelled']
    STATES = UNFINISHED_STATES + FINISHED_STATES

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey('package.id'))
    state = Column(String)
    task_id = Column(Integer)

    def __repr__(self):
        return '{0.id} (name={0.package.name}, state={0.state})'.format(self)
