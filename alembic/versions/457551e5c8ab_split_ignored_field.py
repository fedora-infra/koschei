"""Split ignored field

Revision ID: 457551e5c8ab
Revises: 29c20e85e5fe
Create Date: 2015-07-09 19:04:21.362823

"""

# revision identifiers, used by Alembic.
revision = '457551e5c8ab'
down_revision = '29c20e85e5fe'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('package', sa.Column('blocked', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('package', sa.Column('tracked', sa.Boolean(), server_default='true', nullable=False))
    op.execute("UPDATE package SET tracked=NOT ignored")
    op.drop_column('package', 'ignored')


def downgrade():
    raise NotImplementedError()
