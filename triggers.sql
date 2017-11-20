-- trigger functions
CREATE OR REPLACE FUNCTION update_last_build(pkg_id integer)
    RETURNS void AS $$
DECLARE pkg record;
        lb record;
        lcb record;
BEGIN
    SELECT INTO pkg * FROM package WHERE id = pkg_id;
    SELECT INTO lb * FROM build
        WHERE package_id = pkg_id
          AND NOT deleted
        ORDER BY started DESC
        LIMIT 1;
    IF lb.state = 2 THEN
        SELECT INTO lcb * FROM build
            WHERE package_id = pkg_id
              AND (state = 3 OR state = 5)
              AND NOT deleted
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

CREATE OR REPLACE FUNCTION update_last_build_trigger()
    RETURNS TRIGGER AS $$
BEGIN
    PERFORM update_last_build(NEW.package_id);
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION update_last_build_del()
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

CREATE OR REPLACE FUNCTION update_all_blocked()
    RETURNS TRIGGER AS $$
BEGIN
    UPDATE base_package
    SET all_blocked = q.all_blocked
    FROM (SELECT base_id, BOOL_AND(blocked) AS all_blocked
          FROM package
          GROUP BY base_id) AS q
    WHERE id = q.base_id
        AND base_package.all_blocked IS DISTINCT FROM q.all_blocked;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

-- triggers
DROP TRIGGER IF EXISTS update_last_build_trigger ON build;
CREATE TRIGGER update_last_build_trigger
    AFTER INSERT ON build FOR EACH ROW
    EXECUTE PROCEDURE update_last_build_trigger();
DROP TRIGGER IF EXISTS update_last_build_trigger_up ON build;
CREATE TRIGGER update_last_build_trigger_up
    AFTER UPDATE ON build FOR EACH ROW
    WHEN (OLD.state != NEW.state OR OLD.deleted != NEW.deleted)
    EXECUTE PROCEDURE update_last_build_trigger();
DROP TRIGGER IF EXISTS update_last_build_trigger_del ON build;
CREATE TRIGGER update_last_build_trigger_del
    AFTER DELETE ON build FOR EACH ROW
    EXECUTE PROCEDURE update_last_build_del();
DROP TRIGGER IF EXISTS update_all_blocked_trigger ON package;
CREATE TRIGGER update_all_blocked_trigger
    AFTER INSERT OR DELETE OR UPDATE OF blocked ON package
    FOR EACH STATEMENT
    EXECUTE PROCEDURE update_all_blocked();
