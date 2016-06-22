"""Remove unused package.added column

Revision ID: 51611cd48682
Revises: 2f2fc00d0830
Create Date: 2016-06-22 18:02:02.995670

"""

# revision identifiers, used by Alembic.
revision = '51611cd48682'
down_revision = '2f2fc00d0830'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    op.drop_column('package', 'added')


def downgrade():
    op.add_column('package', sa.Column('added', postgresql.TIMESTAMP(),
                                       autoincrement=False, nullable=False,
                                       server_default="now()"))
