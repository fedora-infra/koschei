"""Make started times non-nullable

Revision ID: 408d2abc986
Revises: e54c706fcae
Create Date: 2016-08-15 15:18:40.648676

"""

# revision identifiers, used by Alembic.
revision = '408d2abc986'
down_revision = 'e54c706fcae'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        ALTER TABLE build ALTER COLUMN started SET NOT NULL;
        ALTER TABLE koji_task ALTER COLUMN started SET NOT NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE build ALTER COLUMN started DROP NOT NULL;
        ALTER TABLE koji_task ALTER COLUMN started DROP NOT NULL;
    """)
