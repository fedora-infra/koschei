"""Collections

Revision ID: 2946d55e18d
Revises: 1cf99f3c1f74
Create Date: 2016-02-17 15:50:24.577533

"""

# revision identifiers, used by Alembic.
revision = '2946d55e18d'
down_revision = '1cf99f3c1f74'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    op.create_table('collection',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=False),
    sa.Column('target_tag', sa.String(), nullable=False),
    sa.Column('build_tag', sa.String(), nullable=False),
    sa.Column('priority_coefficient', sa.Float(), server_default='1', nullable=False),
    sa.Column('latest_repo_id', sa.Integer(), nullable=True),
    sa.Column('latest_repo_resolved', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.execute("INSERT INTO collection VALUES (DEFAULT, 'f25', 'Fedora Rawhide', 'f25', 'f25-build', 1.0, null, null)")
    op.add_column('buildroot_problem', sa.Column('collection_id', sa.Integer(), nullable=True))
    op.create_index('ix_buildroot_problem_collection_id', 'buildroot_problem', ['collection_id'], unique=False)
    op.drop_index('ix_buildroot_problem_repo_id', table_name='buildroot_problem')
    op.drop_constraint(u'buildroot_problem_repo_id_fkey', 'buildroot_problem', type_='foreignkey')
    op.create_foreign_key('buildroot_problem_collection_id_fkey', 'buildroot_problem', 'collection', ['collection_id'], ['id'], ondelete='CASCADE')
    op.drop_column('buildroot_problem', 'repo_id')
    op.add_column('package', sa.Column('collection_id', sa.Integer(), nullable=True))
    op.execute("UPDATE package SET collection_id = 1")
    op.alter_column('package', 'collection_id', nullable=False)
    op.drop_constraint(u'package_name_key', 'package', type_='unique')
    op.create_foreign_key('package_collection_id_fkey', 'package', 'collection', ['collection_id'], ['id'], ondelete='CASCADE')
    op.drop_table('repo_generation_request')
    op.drop_table('repo')


def downgrade():
    raise NotImplementedError()
