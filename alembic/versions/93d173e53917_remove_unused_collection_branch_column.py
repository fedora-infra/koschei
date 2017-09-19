"""
Remove unused collection.branch column

Create Date: 2017-09-13 16:17:48.196576

"""

# revision identifiers, used by Alembic.
revision = '93d173e53917'
down_revision = '6d4894cd2307'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE collection DROP COLUMN branch;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE collection ADD COLUMN branch text;
    """)
