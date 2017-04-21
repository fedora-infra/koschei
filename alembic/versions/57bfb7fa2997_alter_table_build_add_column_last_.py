"""
Alter table build, add column last_complete

Create Date: 2017-04-21 14:57:23.777415

"""

# revision identifiers, used by Alembic.
revision = '57bfb7fa2997'
down_revision = '684eada0e0d6'

from alembic import op


def upgrade():
    op.execute("""
    ALTER TABLE build ADD COLUMN last_complete BOOLEAN;
    ALTER TABLE build ALTER COLUMN last_complete SET DEFAULT FALSE;
    UPDATE build SET last_complete = TRUE WHERE id IN (SELECT MAX(id) FROM build
        WHERE state = 3 OR state = 5 GROUP BY package_id);
    UPDATE build SET last_complete = DEFAULT WHERE last_complete IS NULL;
    ALTER TABLE build ALTER COLUMN last_complete SET NOT NULL;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE build DROP COLUMN last_complete;
    """)
