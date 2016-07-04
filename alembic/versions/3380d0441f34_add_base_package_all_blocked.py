"""Add base_package.all_blocked

Revision ID: 3380d0441f34
Revises: 511bcaac8f6b
Create Date: 2016-07-01 11:07:46.757918

"""

# revision identifiers, used by Alembic.
revision = '3380d0441f34'
down_revision = '511bcaac8f6b'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
        ALTER TABLE base_package ADD COLUMN all_blocked BOOLEAN DEFAULT TRUE NOT NULL;

        CREATE OR REPLACE FUNCTION update_all_blocked()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE base_package
            SET all_blocked = q.all_blocked
            FROM (SELECT base_id, BOOL_AND(blocked) AS all_blocked
                  FROM package
                  GROUP BY base_id) AS q
            WHERE id = q.base_id;
            RETURN NULL;
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER update_all_blocked_trigger
            AFTER INSERT OR DELETE OR UPDATE OF blocked ON package
            FOR EACH STATEMENT
            EXECUTE PROCEDURE update_all_blocked();

        -- execute the trigger once
        UPDATE base_package
        SET all_blocked = q.all_blocked
        FROM (SELECT base_id, BOOL_AND(blocked) AS all_blocked
              FROM package
              GROUP BY base_id) AS q
        WHERE id = q.base_id;
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER IF EXISTS update_all_blocked_trigger;
        DROP PROCEDURE update_all_blocked;
        ALTER TABLE base_package DROP COLUMN all_blocked;
    """)
