"""
Remove current_priority

Create Date: 2017-03-09 13:38:59.186364

"""

# revision identifiers, used by Alembic.
revision = '2cc3e44a68de'
down_revision = '2f4667c1e18f'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE package DROP COLUMN current_priority;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package ADD COLUMN current_priority integer;
    """)
