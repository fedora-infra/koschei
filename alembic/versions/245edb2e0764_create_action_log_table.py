"""
Create action_log table

Create Date: 2017-10-22 11:30:33.000523

"""

# revision identifiers, used by Alembic.
revision = '245edb2e0764'
down_revision = '93d173e53917'

from alembic import op


def upgrade():
    op.execute("""
    CREATE TYPE action_log_environment AS ENUM (
        'admin',
        'backend',
        'frontend'
    );
    CREATE TABLE action_log (
        id SERIAL NOT NULL PRIMARY KEY,
        user_id integer REFERENCES "user",
        environment action_log_environment NOT NULL,
        "timestamp" timestamp without time zone DEFAULT clock_timestamp() NOT NULL,
        message character varying NOT NULL
    );
    """)


def downgrade():
    op.execute("""
    DROP TABLE action_log;
    DROP TYPE action_log_environment;
    """)
