"""
Redo priorities

Create Date: 2017-03-03 12:38:51.669916

"""

# revision identifiers, used by Alembic.
revision = '2f4667c1e18f'
down_revision = '2a0e9d5529c9'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE package ADD COLUMN build_priority integer;
        ALTER TABLE package ADD COLUMN dependency_priority integer;
        UPDATE package SET build_priority = 0, dependency_priority = 0;
        ALTER TABLE package ALTER COLUMN build_priority SET DEFAULT 0;
        ALTER TABLE package ALTER COLUMN build_priority SET NOT NULL;
        ALTER TABLE package ALTER COLUMN dependency_priority SET DEFAULT 0;
        ALTER TABLE package ALTER COLUMN dependency_priority SET NOT NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package DROP COLUMN build_priority;
        ALTER TABLE package DROP COLUMN dependency_priority;
    """)
