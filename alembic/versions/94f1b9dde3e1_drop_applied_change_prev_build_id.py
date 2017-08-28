"""
Drop applied_change.prev_build_id

Create Date: 2017-08-28 14:30:54.584354

"""

# revision identifiers, used by Alembic.
revision = '94f1b9dde3e1'
down_revision = '7ea8ffafe48b'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE applied_change DROP COLUMN prev_build_id;
        ALTER TABLE unapplied_change DROP COLUMN prev_build_id;
    """)


def downgrade():
    # not possible to restore the data
    raise NotImplementedError()
