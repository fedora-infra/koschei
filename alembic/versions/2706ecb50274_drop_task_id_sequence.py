"""Drop task_id sequence

Revision ID: 2706ecb50274
Revises: 4114061e4db2
Create Date: 2016-02-01 14:41:57.098543

"""

# revision identifiers, used by Alembic.
revision = '2706ecb50274'
down_revision = '4114061e4db2'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("DROP SEQUENCE koji_task_task_id_seq CASCADE")


def downgrade():
    pass
