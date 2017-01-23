"""
Remove ix_build_running

Create Date: 2017-01-23 16:01:45.592288

"""

# revision identifiers, used by Alembic.
revision = '96210b1e8db3'
down_revision = '7c789ce47e'

from alembic import op


def upgrade():
    op.execute("""
        DROP INDEX ix_build_running;
    """)


def downgrade():
    op.execute("""
        CREATE UNIQUE INDEX ix_build_running ON build USING btree (package_id) WHERE (state = 2);
    """)
