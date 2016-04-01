"""Add index on package.collection_id

Revision ID: 448688cfd5b6
Revises: 18d4f8beaba6
Create Date: 2016-04-01 14:05:02.049467

"""

# revision identifiers, used by Alembic.
revision = '448688cfd5b6'
down_revision = '18d4f8beaba6'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("DROP INDEX ix_package_collection_id")
    op.execute("CREATE INDEX ix_package_collection_id ON package(collection_id, tracked) WHERE not blocked")


def downgrade():
    raise NotImplementedError()
