from __future__ import print_function

import os
import sys
import logging
import sqlalchemy
import requests

faitout_url = 'http://faitout.fedorainfracloud.org/'

testdir = os.path.dirname(os.path.realpath(__file__))

use_faitout = os.environ.get('TEST_WITH_FAITOUT')
use_postgres = os.environ.get('TEST_WITH_POSTGRES')

os.environ['KOSCHEI_CONFIG'] = '{0}/../config.cfg.template:{0}/test_config.cfg'\
                               .format(testdir)
from koschei import util
assert util.config.get('is_test') is True
if use_faitout:
    req = requests.get(faitout_url + 'new')
    if req.status_code != 200:
        print("Cannot obtain new faitout connection (code={code}): {text}"
              .format(code=req.status_code, text=req.text), file=sys.stderr)
        sys.exit(1)
    util.config['database_url'] = req.text
elif use_postgres:
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


def teardown():
    if use_faitout:
        requests.get(faitout_url + 'drop/' +
                     util.config['database_url'].rsplit('/', 1)[1])
