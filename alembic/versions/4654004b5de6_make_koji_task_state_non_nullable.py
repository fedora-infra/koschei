"""Make koji_task.state non-nullable

Revision ID: 4654004b5de6
Revises: 2176056cfb43
Create Date: 2016-06-10 13:52:20.723229

"""

# revision identifiers, used by Alembic.
revision = '4654004b5de6'
down_revision = '2176056cfb43'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("ALTER TABLE koji_task ALTER COLUMN state SET NOT NULL")


def downgrade():
    op.execute("ALTER TABLE koji_task ALTER COLUMN state DROP NOT NULL")
