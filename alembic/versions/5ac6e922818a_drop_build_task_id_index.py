"""Drop build.task_id index

Revision ID: 5ac6e922818a
Revises: 2706ecb50274
Create Date: 2016-02-01 14:55:19.346585

"""

# revision identifiers, used by Alembic.
revision = '5ac6e922818a'
down_revision = '2706ecb50274'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("DROP INDEX ix_build_task_id")


def downgrade():
    pass
