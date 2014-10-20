"""Foreign key constraint on package.last_build_id

Revision ID: 4507ca8c33d4
Revises: 49f4b34c2712
Create Date: 2014-10-20 10:32:15.844670

"""

# revision identifiers, used by Alembic.
revision = '4507ca8c33d4'
down_revision = '49f4b34c2712'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_foreign_key('fkey_package_last_complete_build_id', 'package', 'build',
                          ['last_complete_build_id'], ['id'])


def downgrade():
    raise NotImplementedError()
