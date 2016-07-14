"""Add DB default to priority fields

Revision ID: 5a5fb752d72b
Revises: 550be6fa6eff
Create Date: 2016-07-14 15:01:01.069584

"""

# revision identifiers, used by Alembic.
revision = '5a5fb752d72b'
down_revision = '550be6fa6eff'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        ALTER TABLE package ALTER COLUMN manual_priority SET DEFAULT 0;
        ALTER TABLE package ALTER COLUMN static_priority SET DEFAULT 0;
        """)


def downgrade():
    # harmless to keep, indempotent to upgrade
    pass
