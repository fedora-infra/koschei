"""
Create index ix_builds_last_complete

Create Date: 2017-04-21 15:01:38.994033

"""

# revision identifiers, used by Alembic.
revision = '95fdd4ca39cb'
down_revision = '57bfb7fa2997'

from alembic import op


def upgrade():
    op.execute("""
    CREATE INDEX ix_builds_last_complete ON build (package_id, task_id) WHERE last_complete;
    """)


def downgrade():
    op.execute("""
    DROP INDEX ix_builds_last_complete;
    """)
