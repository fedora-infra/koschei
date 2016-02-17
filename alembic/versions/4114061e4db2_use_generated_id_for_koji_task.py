"""Use generated id for koji_task

Revision ID: 4114061e4db2
Revises: 3532a68d11c9
Create Date: 2016-02-01 14:31:02.192653

"""

# revision identifiers, used by Alembic.
revision = '4114061e4db2'
down_revision = '3532a68d11c9'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("ALTER TABLE build DROP CONSTRAINT IF EXISTS build_task_id_key")
    op.execute("ALTER TABLE koji_task DROP CONSTRAINT IF EXISTS koji_task_pkey")
    op.execute("ALTER TABLE koji_task ADD COLUMN id SERIAL PRIMARY KEY")


def downgrade():
    raise NotImplementedError()
