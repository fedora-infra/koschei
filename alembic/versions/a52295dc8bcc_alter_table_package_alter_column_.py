"""
ALTER TABLE package ALTER COLUMN tracked SET DEFAULT FALSE

Create Date: 2019-09-18 09:19:44.139999

"""

# revision identifiers, used by Alembic.
revision = 'a52295dc8bcc'
down_revision = '8ab06d9c83e7'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE package ALTER COLUMN tracked SET DEFAULT FALSE;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package ALTER COLUMN tracked SET DEFAULT TRUE;
    """)
