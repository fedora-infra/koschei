"""
Update trigger update_last_complete_build

Create Date: 2017-04-21 15:03:56.590055

"""

# revision identifiers, used by Alembic.
revision = 'be498b0f08fe'
down_revision = '95fdd4ca39cb'

from alembic import op


def upgrade():
    op.execute("""
CREATE OR REPLACE FUNCTION update_last_complete_build()
    RETURNS TRIGGER AS $$
BEGIN
    UPDATE build
    SET last_complete = FALSE
    WHERE last_complete AND package_id = NEW.package_id;
    WITH lcb AS (
        UPDATE build
        SET last_complete = TRUE
        WHERE id = (SELECT MAX(id)
                    FROM build
                    WHERE package_id = NEW.package_id
                          AND (state = 3 OR state = 5))
        RETURNING id, state)
    UPDATE package
    SET last_complete_build_id = lcb.id,
        last_complete_build_state = lcb.state
    FROM lcb
    WHERE package.id = NEW.package_id
        AND last_complete_build_id IS DISTINCT FROM lcb.id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
    """)


def downgrade():
    op.execute("""
CREATE OR REPLACE FUNCTION update_last_complete_build()
    RETURNS TRIGGER AS $$
BEGIN
    UPDATE package
    SET last_complete_build_id = lcb.id,
        last_complete_build_state = lcb.state
    FROM (SELECT id, state
          FROM build
          WHERE package_id = NEW.package_id
                AND (state = 3 OR state = 5)
          ORDER BY id DESC
          LIMIT 1) AS lcb
    WHERE package.id = NEW.package_id
        AND last_complete_build_id IS DISTINCT FROM lcb.id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
    """)
