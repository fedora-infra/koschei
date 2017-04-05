"""
Alter table collection, add constraint collection_latest_repo_id_check

Create Date: 2017-04-05 14:52:03.774589

"""

# revision identifiers, used by Alembic.
revision = '684eada0e0d6'
down_revision = '75d9e5d27ba5'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE collection
        ADD CONSTRAINT collection_latest_repo_id_check
        CHECK (((latest_repo_resolved IS NULL) = (latest_repo_id IS NULL)))
    """)


def downgrade():
    op.execute("""
        ALTER TABLE collection
        DROP CONSTRAINT collection_latest_repo_id_check
    """)
