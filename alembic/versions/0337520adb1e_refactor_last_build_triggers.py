"""
Refactor last_build triggers

Create Date: 2017-11-17 15:42:13.449206

"""

# revision identifiers, used by Alembic.
revision = '0337520adb1e'
down_revision = '8175c5b109d9'

from alembic import op


def upgrade():
    op.execute("""
        DROP TRIGGER update_last_complete_build_trigger ON build;
        DROP TRIGGER update_last_complete_build_trigger_up ON build;
        DROP TRIGGER update_last_build_trigger ON build;
        DROP TRIGGER update_last_build_trigger_del ON build;
        -- This trigger never existed in upstream code, but somehow is present in prod DB
        DROP TRIGGER IF EXISTS update_last_build_trigger_up ON build;
        DROP FUNCTION update_last_complete_build();
        DROP FUNCTION update_last_build();
        DROP FUNCTION update_last_build_del();

        CREATE FUNCTION update_last_build(pkg_id integer)
            RETURNS void AS $$
        DECLARE pkg record;
                lb record;
                lcb record;
        BEGIN
            SELECT INTO pkg * FROM package WHERE id = pkg_id;
            SELECT INTO lb * FROM build
                WHERE package_id = pkg_id
                  AND NOT untagged
                ORDER BY started DESC
                LIMIT 1;
            IF lb.state = 2 THEN
                SELECT INTO lcb * FROM build
                    WHERE package_id = pkg_id
                      AND (state = 3 OR state = 5)
                      AND NOT untagged
                    ORDER BY started DESC
                    LIMIT 1;
            ELSE
                lcb := lb;
            END IF;
            IF pkg.last_build_id IS DISTINCT FROM lb.id THEN
                UPDATE package
                    SET last_build_id = lb.id
                    WHERE package.id = pkg_id;
            END IF;
            IF pkg.last_complete_build_id IS DISTINCT FROM lcb.id THEN
                UPDATE package
                    SET last_complete_build_id = lcb.id,
                        last_complete_build_state = lcb.state
                    WHERE id = pkg_id;
                UPDATE build
                    SET last_complete = FALSE
                    WHERE last_complete AND package_id = pkg_id;
                UPDATE build
                    SET last_complete = TRUE
                    WHERE id = lcb.id;
            END IF;
        END $$ LANGUAGE plpgsql;

        CREATE FUNCTION update_last_build_trigger()
            RETURNS TRIGGER AS $$
        BEGIN
            PERFORM update_last_build(NEW.package_id);
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE FUNCTION update_last_build_del()
            RETURNS TRIGGER AS $$
        BEGIN
            -- try to avoid running more queries than necessary
            -- there's a SET NULL trigger, so if this was the last build then last_build is null
            IF EXISTS (
                    SELECT 1 FROM package
                        WHERE id = OLD.package_id
                          AND last_build_id IS NULL
            ) THEN
                PERFORM update_last_build(OLD.package_id);
            END IF;
            RETURN OLD;
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER update_last_build_trigger
            AFTER INSERT ON build FOR EACH ROW
            EXECUTE PROCEDURE update_last_build_trigger();
        CREATE TRIGGER update_last_build_trigger_up
            AFTER UPDATE ON build FOR EACH ROW
            WHEN (OLD.state != NEW.state OR OLD.untagged != NEW.untagged)
            EXECUTE PROCEDURE update_last_build_trigger();
        CREATE TRIGGER update_last_build_trigger_del
            AFTER DELETE ON build FOR EACH ROW
            EXECUTE PROCEDURE update_last_build_del();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER update_last_build_trigger ON build;
        DROP TRIGGER update_last_build_trigger_up ON build;
        DROP TRIGGER update_last_build_trigger_del ON build;
        DROP FUNCTION update_last_build(integer);
        DROP FUNCTION update_last_build_trigger();
        DROP FUNCTION update_last_build_del();

        CREATE FUNCTION update_last_complete_build()
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

        CREATE FUNCTION update_last_build()
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

        CREATE FUNCTION update_last_build_del()
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

        CREATE TRIGGER update_last_complete_build_trigger
            AFTER INSERT ON build FOR EACH ROW
            WHEN (NEW.state = 3 OR NEW.state = 5)
            EXECUTE PROCEDURE update_last_complete_build();
        CREATE TRIGGER update_last_build_trigger
            AFTER INSERT ON build FOR EACH ROW
            EXECUTE PROCEDURE update_last_build();
        CREATE TRIGGER update_last_complete_build_trigger_up
            AFTER UPDATE ON build FOR EACH ROW
            WHEN (OLD.state != NEW.state)
            EXECUTE PROCEDURE update_last_complete_build();
        CREATE TRIGGER update_last_build_trigger_del
            BEFORE DELETE ON build FOR EACH ROW
            EXECUTE PROCEDURE update_last_build_del();
    """)
