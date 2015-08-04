import os
import sys
import logging
import sqlalchemy

faitout_url = 'http://faitout.cloud.fedoraproject.org/faitout/'

testdir = os.path.dirname(os.path.realpath(__file__))

use_postgres = os.environ.get('TEST_WITH_POSTGRES')

os.chdir(testdir)
os.environ['KOSCHEI_CONFIG'] = '{0}/../config.cfg.template:{0}/test_config.cfg'\
                               .format(testdir)
from koschei import util
assert util.config.get('is_test') is True
if use_postgres:
    testdb = 'koschei_testdb'
    util.config['database_config']['drivername'] = 'postgres'
    util.config['database_config']['username'] = 'postgres'
    util.config['database_config']['database'] = testdb
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
else:
    util.config['database_config']['drivername'] = 'sqlite'

from koschei import models
models.Base.metadata.create_all(models.engine)

sql_log = logging.getLogger('sqlalchemy.engine')
sql_log.propagate = False
sql_log.setLevel(logging.INFO)
sql_log_file = 'sql.log'
sql_log.addHandler(logging.FileHandler(sql_log_file))

def teardown():
    pass
