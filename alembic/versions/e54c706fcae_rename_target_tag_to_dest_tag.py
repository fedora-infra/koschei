"""Rename target_tag to dest_tag

Revision ID: e54c706fcae
Revises: 1cd95cdf6712
Create Date: 2016-07-21 09:54:30.700968

"""

# revision identifiers, used by Alembic.
revision = 'e54c706fcae'
down_revision = '1cd95cdf6712'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("ALTER TABLE collection RENAME COLUMN target_tag TO dest_tag")


def downgrade():
    op.execute("ALTER TABLE collection RENAME COLUMN dest_tag TO target_tag")
