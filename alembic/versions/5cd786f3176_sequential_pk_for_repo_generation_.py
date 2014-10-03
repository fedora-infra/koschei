"""Sequential PK for repo_generation_request

Revision ID: 5cd786f3176
Revises: 498aaa82048d
Create Date: 2014-10-03 10:25:00.782234

"""

# revision identifiers, used by Alembic.
revision = '5cd786f3176'
down_revision = '498aaa82048d'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    op.drop_table(u'repo_generation_request')
    op.create_table('repo_generation_request',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('repo_id', sa.Integer(), nullable=False),
    sa.Column('requested', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    raise NotImplementedError()
