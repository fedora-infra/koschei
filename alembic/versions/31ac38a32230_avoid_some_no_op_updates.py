"""
Avoid some no-op updates

Create Date: 2016-11-18 14:05:47.218200

"""

# revision identifiers, used by Alembic.
revision = '31ac38a32230'
down_revision = '49d84bc25b26'

from alembic import op


def upgrade():
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
    WHERE package.id = NEW.package_id;
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
    WHERE package.id = NEW.package_id;
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
    WHERE package.id = OLD.package_id;
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
    WHERE id = q.base_id;
    RETURN NULL;
END $$ LANGUAGE plpgsql;
    """)
