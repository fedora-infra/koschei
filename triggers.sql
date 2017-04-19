-- trigger functions
CREATE OR REPLACE FUNCTION update_last_complete_build()
    RETURNS TRIGGER AS $$
BEGIN
    -- TODO migrations
    UPDATE build
    SET last_complete = FALSE
    WHERE last_complete AND package_id = NEW.package_id;
    UPDATE build
    SET last_complete = TRUE
    WHERE id = (SELECT MAX(id)
                FROM build
		WHERE package_id = NEW.package_id
		      AND (state = 3 OR state = 5));
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
DROP TRIGGER IF EXISTS update_last_complete_build_trigger ON build;
CREATE TRIGGER update_last_complete_build_trigger
    AFTER INSERT ON build FOR EACH ROW
    WHEN (NEW.state = 3 OR NEW.state = 5)
    EXECUTE PROCEDURE update_last_complete_build();
DROP TRIGGER IF EXISTS update_last_build_trigger ON build;
CREATE TRIGGER update_last_build_trigger
    AFTER INSERT ON build FOR EACH ROW
    EXECUTE PROCEDURE update_last_build();
DROP TRIGGER IF EXISTS update_last_complete_build_trigger_up ON build;
CREATE TRIGGER update_last_complete_build_trigger_up
    AFTER UPDATE ON build FOR EACH ROW
    WHEN (OLD.state != NEW.state)
    EXECUTE PROCEDURE update_last_complete_build();
DROP TRIGGER IF EXISTS update_last_build_trigger_del ON build;
CREATE TRIGGER update_last_build_trigger_del
    BEFORE DELETE ON build FOR EACH ROW
    EXECUTE PROCEDURE update_last_build_del();
DROP TRIGGER IF EXISTS update_all_blocked_trigger ON package;
CREATE TRIGGER update_all_blocked_trigger
    AFTER INSERT OR DELETE OR UPDATE OF blocked ON package
    FOR EACH STATEMENT
    EXECUTE PROCEDURE update_all_blocked();
