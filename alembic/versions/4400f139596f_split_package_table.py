"""Split package table

Revision ID: 4400f139596f
Revises: 51611cd48682
Create Date: 2016-06-30 13:48:30.803926

"""

# revision identifiers, used by Alembic.
revision = '4400f139596f'
down_revision = '51611cd48682'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('base_package',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.add_column(u'package', sa.Column('base_id', sa.Integer(), nullable=True))
    op.execute("""
    INSERT INTO base_package(name) SELECT DISTINCT name FROM package;
    CREATE INDEX ix_package_name ON package(name);
    UPDATE package SET base_id = q.id FROM (SELECT id, name FROM base_package) as q WHERE package.name = q.name;
    ALTER TABLE package ALTER COLUMN base_id SET NOT NULL;
    """)
    op.create_unique_constraint('package_unique_in_collection', 'package', ['base_id', 'collection_id'])
    op.create_foreign_key('fkey_package_base_id', 'package', 'base_package', ['base_id'], ['id'], ondelete='CASCADE')


def downgrade():
    op.drop_constraint('fkey_package_base_id', 'package', type_='foreignkey')
    op.drop_constraint('package_unique_in_collection', 'package', type_='unique')
    op.execute("DROP INDEX ix_package_name")
    op.drop_column(u'package', 'base_id')
    op.drop_table('base_package')
