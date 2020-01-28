"""
Alter table collection, add column scm_url

Create Date: 2020-01-28 11:32:32.028819

"""

# revision identifiers, used by Alembic.
revision = 'a8dbdfdc3239'
down_revision = '0d2a8d58a582'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE collection ADD COLUMN scm_url character varying;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE collection DROP COLUMN scm_url;
    """)
