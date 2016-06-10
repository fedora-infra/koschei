"""Merge deps_processed and deps_resolved

Revision ID: 2f2fc00d0830
Revises: 4654004b5de6
Create Date: 2016-06-10 13:57:17.692066

"""

# revision identifiers, used by Alembic.
revision = '2f2fc00d0830'
down_revision = '4654004b5de6'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("ALTER TABLE build ALTER COLUMN deps_resolved DROP NOT NULL")
    op.execute("ALTER TABLE build ALTER COLUMN deps_resolved DROP DEFAULT")
    op.execute("UPDATE build SET deps_resolved = NULL WHERE NOT deps_processed")
    op.execute("ALTER TABLE build DROP COLUMN deps_processed")

def downgrade():
    op.execute("ALTER TABLE build ADD COLUMN deps_processed boolean")
    op.execute("UPDATE build SET deps_processed = CASE WHEN deps_resolved IS NULL THEN FALSE ELSE TRUE END")
    op.execute("UPDATE build SET deps_resolved = FALSE WHERE deps_resolved IS NULL")
    op.execute("ALTER TABLE build ALTER COLUMN deps_resolved SET DEFAULT FALSE")
    op.execute("ALTER TABLE build ALTER COLUMN deps_resolved SET NOT NULL")
    op.execute("ALTER TABLE build ALTER COLUMN deps_processed SET NOT NULL")
