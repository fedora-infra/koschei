"""
Remove collection.dest_tag

Create Date: 2017-11-20 13:51:34.220593

"""

# revision identifiers, used by Alembic.
revision = '780c0fed288f'
down_revision = '0337520adb1e'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE collection DROP COLUMN dest_tag;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE collection ADD COLUMN dest_tag text;
        UPDATE collection SET dest_tag = build_tag;
        ALTER TABLE collection ALTER COLUMN dest_tag SET NOT NULL;
    """)
