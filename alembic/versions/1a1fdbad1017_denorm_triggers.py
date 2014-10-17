"""Denorm triggers

Revision ID: 1a1fdbad1017
Revises: 195b4306b2e
Create Date: 2014-10-17 13:33:45.459503

"""

# revision identifiers, used by Alembic.
revision = '1a1fdbad1017'
down_revision = '195b4306b2e'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION update_last_complete_build()
                      RETURNS TRIGGER AS $$
                  BEGIN
                      UPDATE package
                      SET last_complete_build_id = lcb.id
                      FROM (SELECT id, task_id, state, started
                            FROM build
                            WHERE package_id = NEW.package_id
                                  AND (state = 3 OR state = 5)
                            ORDER BY task_id DESC
                            LIMIT 1) AS lcb
                      WHERE package.id = NEW.package_id;
                      RETURN NEW;
                  END $$ LANGUAGE plpgsql;

                  DROP TRIGGER IF EXISTS update_last_complete_build_trigger ON build;
                  CREATE TRIGGER update_last_complete_build_trigger
                      AFTER INSERT OR UPDATE ON build FOR EACH ROW
                      EXECUTE PROCEDURE update_last_complete_build();

                  CREATE OR REPLACE FUNCTION update_resolved()
                      RETURNS TRIGGER AS $$
                  BEGIN
                      UPDATE package
                      SET resolved = lr.resolved
                      FROM (SELECT resolved
                            FROM resolution_result
                            WHERE package_id = NEW.package_id
                            ORDER BY repo_id DESC
                            LIMIT 1) AS lr
                      WHERE package.id = NEW.package_id;
                      RETURN NEW;
                  END $$ LANGUAGE plpgsql;

                  DROP TRIGGER IF EXISTS update_resolved_trigger ON resolution_result;
                  CREATE TRIGGER update_resolved_trigger
                      AFTER INSERT OR UPDATE ON resolution_result FOR EACH ROW
                      EXECUTE PROCEDURE update_resolved();
                  """)
    op.execute("""UPDATE build SET real=real
               WHERE id in (SELECT DISTINCT ON (package_id) id FROM build
                            ORDER BY package_id, task_id DESC)""")
    op.execute("""UPDATE resolution_result SET resolved=resolved WHERE id in
               (SELECT DISTINCT ON (package_id) id FROM resolution_result
               ORDER BY package_id, id DESC)""")


def downgrade():
    raise NotImplementedError()
