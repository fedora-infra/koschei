import os
import unittest
import sqlalchemy
import logging
import shutil
import json

from datetime import datetime

testdir = os.path.dirname(os.path.realpath(__file__))
datadir = os.path.join(testdir, 'data')
os.chdir(testdir)

use_postgres = os.environ.get('TEST_WITH_POSTGRES')

default_cfg = os.path.join(testdir, '../config.cfg.template')
test_cfg = os.path.join(testdir, 'test_config.cfg')
os.environ['KOSCHEI_CONFIG'] = default_cfg + ':' + test_cfg
from koschei import util
assert util.config.get('is_test') is True
if use_postgres:
    testdb = 'koschei_testdb'
    util.config['database_config']['drivername'] = 'postgres'
    util.config['database_config']['username'] = 'postgres'
    util.config['database_config']['database'] = testdb
else:
    util.config['database_config']['drivername'] = 'sqlite'

sql_log = logging.getLogger('sqlalchemy.engine')
sql_log.propagate = False
sql_log.setLevel(logging.INFO)
sql_log_file = 'sql.log'
sql_log.addHandler(logging.FileHandler(sql_log_file))

from koschei import models as m

class MockDatetime(object):
    @staticmethod
    def now():
        return datetime(2000, 10, 10)

workdir = '.workdir'

def postgres_only(fn):
    return unittest.skipIf(not use_postgres, "Requires postgres")(fn)

class AbstractTest(unittest.TestCase):

    def __init__(self, *args, **kwargs):
        super(AbstractTest, self).__init__(*args, **kwargs)

    def _rm_workdir(self):
        try:
            shutil.rmtree(workdir)
        except OSError:
            pass

    def setUp(self):
        self._rm_workdir()
        os.mkdir(workdir)
        os.chdir(workdir)

    def tearDown(self):
        self._rm_workdir()

    @staticmethod
    def get_json_data(name):
        with open(os.path.join(datadir, name)) as fo:
            return json.load(fo)

class DBTest(AbstractTest):
    def __init__(self, *args, **kwargs):
        super(DBTest, self).__init__(*args, **kwargs)
        self.s = None
        self.inited = False
        self.task_id_counter = 1

        if use_postgres:
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

    def setUp(self):
        super(DBTest, self).setUp()
        if not self.inited:
            m.Base.metadata.create_all(m.engine)
            self.inited = True
        tables = m.Base.metadata.tables
        conn = m.engine.connect()
        for table in tables.values():
            conn.execute(table.delete())
        conn.close()
        self.s = m.Session()

    def tearDown(self):
        super(DBTest, self).tearDown()
        self.s.close()
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

    def prepare_packages(self, pkg_names):
        pkgs = []
        for name in pkg_names:
            pkg = self.s.query(m.Package).filter_by(name=name).first()
            if not pkg:
                pkg = m.Package(name=name)
                self.s.add(pkg)
            pkgs.append(pkg)
        self.s.commit()
        return pkgs

    def prepare_builds(self, repo_id, **builds):
        new_builds = []
        for pkg_name, state in sorted(builds.items()):
            states = {
                    True: m.Build.COMPLETE,
                    False: m.Build.FAILED,
                    None: m.Build.RUNNING,
                    }
            if isinstance(state, bool):
                state = states[state]
            package_id = self.s.query(m.Package.id).filter_by(name=pkg_name).scalar()
            build = m.Build(package_id=package_id, state=state, repo_id=repo_id,
                            task_id=self.task_id_counter)
            self.task_id_counter += 1
            self.s.add(build)
            new_builds.append(build)
        self.s.commit()
        return new_builds

    @staticmethod
    def parse_pkg(string):
        epoch = None
        if ':' in string:
            epoch, _, string = string.partition(':')
        name, version, release = string.rsplit('-', 2)
        return dict(epoch=epoch, name=name, version=version, release=release, arch='x86_64')
