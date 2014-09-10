"""Drop repo table, use only koji repo_id

Revision ID: 585bbd75f3bc
Revises: a0da55358f4
Create Date: 2014-09-10 09:57:45.790221

"""

# revision identifiers, used by Alembic.
revision = '585bbd75f3bc'
down_revision = 'a0da55358f4'

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.drop_constraint('resolution_result_repo_id_fkey', 'resolution_result')
    op.drop_constraint('dependency_repo_id_fkey', 'dependency')

    op.alter_column('dependency', u'repo_id',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.add_column('resolution_result', sa.Column('generated', sa.DateTime(), nullable=False,
                                                 server_default=sa.func.now()))
    op.execute("""DELETE FROM resolution_result WHERE repo_id is NULL""")
    op.alter_column('resolution_result', u'repo_id',
                    existing_type=sa.INTEGER(),
                    nullable=False)
    op.drop_table(u'repo')

def downgrade():
    raise NotImplementedError()
