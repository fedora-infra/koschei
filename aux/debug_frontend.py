#!/usr/bin/python

import logging
import time
from sqlalchemy import event
from sqlalchemy.engine import Engine
from koschei.config import load_config

load_config(['config.cfg.template', 'aux/test-config.cfg'])

from koschei.frontend import app as application
import koschei.frontend.views
import koschei.frontend.auth

logger = logging.getLogger("koschei.sql")
logger.setLevel(logging.DEBUG)


@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, 
                        parameters, context, executemany):
    context._query_start_time = time.time()
    logger.debug("Start Query:\n%s" % statement)
    logger.debug("Parameters:\n%r" % (parameters,))


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, 
                        parameters, context, executemany):
    total = time.time() - context._query_start_time
    logger.debug("Query Complete!")

    logger.debug("Total Time: %.02fms" % (total*1000))


application.run(debug=True)
