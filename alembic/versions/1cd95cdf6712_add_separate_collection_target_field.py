"""Add separate collection.target field

Revision ID: 1cd95cdf6712
Revises: 48ef45e53492
Create Date: 2016-07-19 13:22:04.898981

"""

# revision identifiers, used by Alembic.
revision = '1cd95cdf6712'
down_revision = '48ef45e53492'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        ALTER TABLE collection ADD COLUMN target CHARACTER VARYING;
        UPDATE collection SET target = target_tag;
        ALTER TABLE collection ALTER COLUMN target SET NOT NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE collection DROP COLUMN target;
    """)
