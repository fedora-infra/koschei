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
    CREATE TYPE log_environment AS ENUM (
        'admin',
        'backend',
        'frontend'
    );
    CREATE TABLE log_entry (
        id SERIAL NOT NULL PRIMARY KEY,
        user_id integer REFERENCES "user",
        base_id integer REFERENCES base_package,
        environment log_environment NOT NULL,
        "timestamp" timestamp without time zone DEFAULT clock_timestamp() NOT NULL,
        message character varying NOT NULL,
        CONSTRAINT log_entry_user_id_check
            CHECK (((user_id IS NOT NULL) OR (environment = 'backend'::log_environment)))
    );
    """)


def downgrade():
    op.execute("""
    DROP TABLE log_entry;
    DROP TYPE log_environment;
    """)
