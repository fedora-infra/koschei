"""Add collection_group

Revision ID: 48ef45e53492
Revises: 5a5fb752d72b
Create Date: 2016-07-18 12:51:49.489866

"""

# revision identifiers, used by Alembic.
revision = '48ef45e53492'
down_revision = '5a5fb752d72b'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        CREATE TABLE collection_group (
            id SERIAL PRIMARY KEY,
            name character varying NOT NULL UNIQUE,
            display_name character varying NOT NULL
        );
        CREATE TABLE collection_group_relation (
            group_id integer REFERENCES collection_group(id) ON DELETE CASCADE,
            collection_id integer REFERENCES collection(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, collection_id)
        );
    """)



def downgrade():
    op.execute("""
        DROP TABLE collection_group_relation;
        DROP TABLE collection_group;
    """)
