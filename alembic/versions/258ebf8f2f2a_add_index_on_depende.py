"""Add index on dependency.package_id

Revision ID: 258ebf8f2f2a
Revises: 4c8bb5a1cf4a
Create Date: 2014-08-27 20:15:38.367824

"""

# revision identifiers, used by Alembic.
revision = '258ebf8f2f2a'
down_revision = '4c8bb5a1cf4a'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_index('ix_dependency_package_id', 'dependency', ['package_id'])


def downgrade():
    op.drop_index('ix_dependency_package_id')

