import os
import sys
import unittest
import sqlalchemy

testdir = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [os.path.join(testdir, '..'),
                os.path.join(testdir, 'mocks')]

# our mock
import fedmsg

os.environ['KOSCHEI_CONFIG'] = os.path.join(testdir, 'test_config.cfg')
from koschei import util
assert util.config.get('is_test') is True
testdb = 'koschei_testdb'
util.config['database_config']['database'] = testdb

from koschei import service

def identity_decorator(*args, **kwargs):
    def decorator(function):
        return function
    return decorator

service.service_main = identity_decorator

from koschei import models as m

class AbstractTest(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(AbstractTest, self).__init__(*args, **kwargs)
        self.fedmsg = fedmsg

        cfg = util.config['database_config'].copy()
        del cfg['database']
        url = sqlalchemy.engine.url.URL(**cfg)
        engine = sqlalchemy.create_engine(url, poolclass=sqlalchemy.pool.NullPool)
        conn = engine.connect()
        conn.execute("COMMIT")
        conn.execute("DROP DATABASE IF EXISTS {0}".format(testdb))
        conn.execute("COMMIT")
        conn.execute("CREATE DATABASE {0}".format(testdb))
        conn.close()
        self.s = None

    def setUp(self):
        m.Base.metadata.create_all(m.engine)
        tables = m.Base.metadata.tables
        conn = m.engine.connect()
        for table in tables.values():
            conn.execute(table.delete())
        conn.close()
        self.s = m.Session()
        self.fedmsg.mock_init()

    def tearDown(self):
        self.s.close()
        self.fedmsg.mock_verify_empty()
        m.engine.dispose()

    def prepare_basic_data(self):
        pkg = m.Package(name='rnv')
        self.s.add(pkg)
        self.s.flush()
        build = m.Build(package_id=pkg.id, state=m.Build.RUNNING,
                        task_id=666)
        self.s.add(build)
        self.s.commit()
        return pkg, build
