"""Use base_package in package_group_relation

Revision ID: 511bcaac8f6b
Revises: 4400f139596f
Create Date: 2016-07-01 09:41:20.478702

"""

# revision identifiers, used by Alembic.
revision = '511bcaac8f6b'
down_revision = '4400f139596f'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        ALTER TABLE package_group_relation ADD COLUMN base_id INTEGER;
        UPDATE package_group_relation SET base_id = base_package.id
               FROM base_package WHERE name = package_name;
        DELETE FROM package_group_relation WHERE base_id IS NULL;
        DROP INDEX ix_package_group_relation_package_name;
        DROP INDEX ix_package_group_relation_group_id;
        ALTER TABLE package_group_relation DROP COLUMN package_name;
        CREATE INDEX ix_package_group_relation_base_id ON package_group_relation(base_id);
        ALTER TABLE package_group_relation ALTER COLUMN base_id SET NOT NULL;
        ALTER TABLE package_group_relation ADD CONSTRAINT package_group_relation_base_id_fkey
              FOREIGN KEY (base_id) REFERENCES base_package(id) ON DELETE CASCADE;
        -- also creates index
        ALTER TABLE package_group_relation ADD PRIMARY KEY (group_id, base_id);
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package_group_relation ADD COLUMN package_name CHARACTER VARYING;
        ALTER TABLE package_group_relation DROP CONSTRAINT package_group_relation_pkey;
        UPDATE package_group_relation SET package_name = base_package.name
               FROM base_package WHERE base_id = base_package.id;
        DROP INDEX ix_package_group_relation_base_id;
        ALTER TABLE package_group_relation DROP COLUMN base_id;
        ALTER TABLE package_group_relation ALTER COLUMN package_name SET NOT NULL;
        -- there was no PK
        CREATE INDEX ix_package_group_relation_package_name ON package_group_relation(package_name);
        CREATE INDEX ix_package_group_relation_group_id ON package_group_relation(group_id);
    """)
