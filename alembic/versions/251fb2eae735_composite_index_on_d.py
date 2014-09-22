"""Composite index on dependency

Revision ID: 251fb2eae735
Revises: 1867c701b
Create Date: 2014-09-22 17:09:31.064350

"""

# revision identifiers, used by Alembic.
revision = '251fb2eae735'
down_revision = '1867c701b'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_index('ix_dependency_package_id')
    op.create_index('ix_dependency_composite', 'dependency', ['package_id', 'repo_id'])

def downgrade():
    raise NotImplementedError()
