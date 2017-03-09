"""
Add package.latest_repo_id

Create Date: 2017-03-09 16:25:36.300565

"""

# revision identifiers, used by Alembic.
revision = 'abad9d26c83a'
down_revision = '2cc3e44a68de'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE package ADD COLUMN latest_repo_id integer;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package DROP COLUMN latest_repo_id;
    """)
