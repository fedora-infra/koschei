"""Split dependency changes table

Revision ID: 14ef9d47d314
Revises: 31d647dbc4c5
Create Date: 2015-09-07 16:23:42.789628

"""

# revision identifiers, used by Alembic.
revision = '14ef9d47d314'
down_revision = '31d647dbc4c5'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('unapplied_change',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('dep_name', sa.String(), nullable=False),
    sa.Column('prev_epoch', sa.Integer(), nullable=True),
    sa.Column('prev_version', sa.String(), nullable=True),
    sa.Column('prev_release', sa.String(), nullable=True),
    sa.Column('curr_epoch', sa.Integer(), nullable=True),
    sa.Column('curr_version', sa.String(), nullable=True),
    sa.Column('curr_release', sa.String(), nullable=True),
    sa.Column('distance', sa.Integer(), nullable=True),
    sa.Column('package_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['package_id'], ['package.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_unapplied_change_package_id'), 'unapplied_change', ['package_id'], unique=False)
    op.execute("""
               ALTER TABLE dependency_change RENAME TO applied_change;
               DELETE FROM applied_change WHERE applied_in_id IS NULL;
               ALTER TABLE applied_change RENAME COLUMN applied_in_id TO build_id;
               ALTER TABLE applied_change ALTER COLUMN build_id SET NOT NULL;
               ALTER TABLE applied_change DROP COLUMN package_id;
               DROP INDEX ix_dependency_change_applied_in_id;
               """)
    op.create_index(op.f('ix_applied_change_build_id'), 'applied_change', ['build_id'], unique=False)


def downgrade():
    raise NotImplementedError()
