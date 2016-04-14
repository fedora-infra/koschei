from __future__ import print_function

import os
import sys
import logging
import platform
import sqlalchemy
import requests

from koschei import models
from koschei.config import load_config, get_config

faitout_url = 'http://faitout.fedorainfracloud.org/'

testdir = os.path.dirname(os.path.realpath(__file__))

use_faitout = os.environ.get('TEST_WITH_FAITOUT')
use_postgres = os.environ.get('TEST_WITH_POSTGRES')
postgres_host = os.environ.get('POSTGRES_HOST')

is_x86_64 = platform.machine() == 'x86_64'

load_config(['{0}/../config.cfg.template'.format(testdir),
             '{0}/test_config.cfg'.format(testdir)])

config = get_config(None)

if use_faitout:
    req = requests.get(faitout_url + 'new')
    if req.status_code != 200:
        print("Cannot obtain new faitout connection (code={code}): {text}"
              .format(code=req.status_code, text=req.text), file=sys.stderr)
        sys.exit(1)
    config['database_url'] = req.text
elif use_postgres:
    testdb = 'koschei_testdb'
    config['database_config']['drivername'] = 'postgres'
    config['database_config']['database'] = testdb
    if postgres_host:
        config['database_config']['host'] = postgres_host
    cfg = config['database_config'].copy()
    cfg['database'] = 'postgres'
    url = sqlalchemy.engine.url.URL(**cfg)
    config['database_url'] = url
    engine = sqlalchemy.create_engine(url, poolclass=sqlalchemy.pool.NullPool)
    conn = engine.connect()
    conn.execute("COMMIT")
    conn.execute("DROP DATABASE IF EXISTS {0}".format(testdb))
    conn.execute("COMMIT")
    conn.execute("CREATE DATABASE {0}".format(testdb))
    conn.close()

if use_postgres or use_faitout:
    models.Base.metadata.create_all(models.get_engine())


def teardown():
    if use_faitout:
        requests.get(faitout_url + 'drop/' +
                     config['database_url'].rsplit('/', 1)[1])
