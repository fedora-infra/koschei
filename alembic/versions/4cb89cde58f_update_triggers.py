"""Update triggers

Revision ID: 4cb89cde58f
Revises: 3aca44fabc38
Create Date: 2015-09-08 12:43:57.363999

"""

# revision identifiers, used by Alembic.
revision = '4cb89cde58f'
down_revision = '3aca44fabc38'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
              CREATE OR REPLACE FUNCTION update_last_complete_build()
                  RETURNS TRIGGER AS $$
              BEGIN
                  UPDATE package
                  SET last_complete_build_id = lcb.id
                  FROM (SELECT id, state, started
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

              DROP TRIGGER IF EXISTS update_last_complete_build_trigger
                    ON build;
              DROP TRIGGER IF EXISTS update_last_build_trigger
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  WHEN (NEW.state = 3 OR NEW.state = 5)
                  EXECUTE PROCEDURE update_last_complete_build();
              CREATE TRIGGER update_last_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  EXECUTE PROCEDURE update_last_build();
              DROP TRIGGER IF EXISTS update_last_complete_build_trigger_up
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger_up
                  AFTER UPDATE ON build FOR EACH ROW
                  WHEN (OLD.state != NEW.state)
                  EXECUTE PROCEDURE update_last_complete_build();
              DROP TRIGGER IF EXISTS update_last_build_trigger_del
                    ON build;
              CREATE TRIGGER update_last_build_trigger_del
                  BEFORE DELETE ON build FOR EACH ROW
                  EXECUTE PROCEDURE update_last_build_del();
""")


def downgrade():
    raise NotImplementedError()
