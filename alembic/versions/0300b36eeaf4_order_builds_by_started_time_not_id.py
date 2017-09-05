"""
Order builds by started time, not id

Create Date: 2017-09-05 17:24:00.788447

"""

# revision identifiers, used by Alembic.
revision = '0300b36eeaf4'
down_revision = '94f1b9dde3e1'

from alembic import op


def upgrade():
    op.execute("""
        DROP INDEX ix_build_composite;
        CREATE INDEX ix_build_composite ON build(package_id, started DESC);

        -- trigger functions
        CREATE OR REPLACE FUNCTION update_last_complete_build()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE build
            SET last_complete = FALSE
            WHERE last_complete AND package_id = NEW.package_id;
            WITH lcb AS (
                UPDATE build
                SET last_complete = TRUE
                WHERE id = (SELECT id
                            FROM build
                            WHERE package_id = NEW.package_id
                                  AND (state = 3 OR state = 5)
                            ORDER BY started DESC
                            LIMIT 1)
                RETURNING id, state)
            UPDATE package
            SET last_complete_build_id = lcb.id,
                last_complete_build_state = lcb.state
            FROM lcb
            WHERE package.id = NEW.package_id
                AND last_complete_build_id IS DISTINCT FROM lcb.id;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION update_last_build()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE package
            SET last_build_id = lb.id
            FROM (SELECT id, state, started
                  FROM build
                  WHERE package_id = NEW.package_id
                  ORDER BY started DESC
                  LIMIT 1) AS lb
            WHERE package.id = NEW.package_id
                AND last_build_id IS DISTINCT FROM lb.id;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION update_last_build_del()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE package
            SET last_build_id = lb.id
            FROM (SELECT id, state, started
                  FROM build
                  WHERE package_id = OLD.package_id
                        AND build.id != OLD.id
                  ORDER BY started DESC
                  LIMIT 1) AS lb
            WHERE package.id = OLD.package_id
                AND last_build_id IS DISTINCT FROM lb.id;
            RETURN OLD;
        END $$ LANGUAGE plpgsql;
    """)


def downgrade():
    op.execute("""
        DROP INDEX ix_build_composite;
        CREATE INDEX ix_build_composite ON build(package_id, id DESC);

        -- trigger functions
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

        CREATE OR REPLACE FUNCTION update_last_build()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE package
            SET last_build_id = lb.id
            FROM (SELECT id, state, started
                  FROM build
                  WHERE package_id = NEW.package_id
                  ORDER BY id DESC
                  LIMIT 1) AS lb
            WHERE package.id = NEW.package_id
                AND last_build_id IS DISTINCT FROM lb.id;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION update_last_build_del()
            RETURNS TRIGGER AS $$
        BEGIN
            UPDATE package
            SET last_build_id = lb.id
            FROM (SELECT id, state, started
                  FROM build
                  WHERE package_id = OLD.package_id
                        AND build.id != OLD.id
                  ORDER BY id DESC
                  LIMIT 1) AS lb
            WHERE package.id = OLD.package_id
                AND last_build_id IS DISTINCT FROM lb.id;
            RETURN OLD;
        END $$ LANGUAGE plpgsql;
    """)
