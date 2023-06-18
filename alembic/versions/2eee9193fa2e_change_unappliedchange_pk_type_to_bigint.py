"""
Change UnappliedChange PK type to bigint

Create Date: 2023-06-20 11:37:51.119244

"""

# revision identifiers, used by Alembic.
revision = '2eee9193fa2e'
down_revision = '6804dc8bf7b1'

from alembic import op


def upgrade():
    op.execute("""
    DELETE FROM unapplied_change;
    ALTER TABLE unapplied_change ALTER COLUMN id TYPE bigint;
    ALTER SEQUENCE unapplied_change_id_seq AS bigint RESTART;
    """)


def downgrade():
    op.execute("""
    DELETE FROM unapplied_change;
    ALTER TABLE unapplied_change ALTER COLUMN id TYPE integer;
    ALTER SEQUENCE unapplied_change_id_seq AS integer RESTART;
    """)
