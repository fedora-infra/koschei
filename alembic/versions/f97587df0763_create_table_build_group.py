"""
Create table build_group

Create Date: 2020-02-25 18:20:12.352212

"""

# revision identifiers, used by Alembic.
revision = 'f97587df0763'
down_revision = '0d2a8d58a582'

from alembic import op


def upgrade():
    op.execute("""

CREATE SEQUENCE build_group_id_seq;

CREATE TABLE build_group (
    id integer NOT NULL DEFAULT nextval('build_group_id_seq'::regclass),
    repo_id integer,
    collection_id integer,
    base_collection_id integer,
    state character varying,
    CONSTRAINT build_group_pkey PRIMARY KEY (id),
    CONSTRAINT build_group_base_collection_id_fkey FOREIGN KEY (base_collection_id) REFERENCES collection(id) ON DELETE SET NULL,
    CONSTRAINT build_group_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES collection(id) ON DELETE SET NULL
);

    """)


def downgrade():
    op.execute("""

DROP TABLE build_group;

    """)
