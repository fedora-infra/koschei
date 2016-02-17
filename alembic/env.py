from __future__ import with_statement
import os
from alembic import context
from logging.config import fileConfig

# FIXME we should have some nicer solution to override configs
if 'KOSCHEI_CONFIG' not in os.environ:
    os.environ['KOSCHEI_CONFIG'] = '/usr/share/koschei/config.cfg:/etc/koschei/config.cfg:/etc/koschei/config-admin.cfg'

from koschei.models import Base, grant_db_access

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    from koschei.models import db_url

    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=db_url)

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    from koschei.models import engine

    connection = engine.connect()
    context.configure(
                connection=connection,
                target_metadata=target_metadata
                )

    try:
        with context.begin_transaction():
            context.run_migrations()
            grant_db_access(None, connection)
    finally:
        connection.close()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

