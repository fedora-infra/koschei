"""
Create partial index ix_builds_unprocessed

Create Date: 2017-04-05 11:50:52.185886

"""

# revision identifiers, used by Alembic.
revision = '75d9e5d27ba5'
down_revision = '2cc3e44a68de'

from alembic import op


def upgrade():
    op.execute("""
        CREATE INDEX ix_builds_unprocessed ON build (task_id)
        WHERE deps_resolved IS NULL AND repo_id IS NOT NULL
    """)


def downgrade():
    op.execute("""
        DROP INDEX ix_builds_unprocessed
    """)
