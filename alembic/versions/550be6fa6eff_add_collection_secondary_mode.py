"""Add collection.secondary_mode

Revision ID: 550be6fa6eff
Revises: 3380d0441f34
Create Date: 2016-07-07 12:35:38.957699

"""

# revision identifiers, used by Alembic.
revision = '550be6fa6eff'
down_revision = '3380d0441f34'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("ALTER TABLE collection ADD COLUMN secondary_mode BOOLEAN DEFAULT FALSE NOT NULL")


def downgrade():
    op.execute("ALTER TABLE collection DROP COLUMN secondary_mode")
