"""Order builds by id instead of task_id

Revision ID: 250e19717740
Revises: 4be96f2685d
Create Date: 2015-03-16 14:02:10.712506

"""

# revision identifiers, used by Alembic.
revision = '250e19717740'
down_revision = '4be96f2685d'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_index('ix_build_composite', table_name='build')
    op.execute('CREATE INDEX ix_build_composite ON build(package_id, id DESC)')
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

              DROP TRIGGER IF EXISTS update_last_complete_build_trigger
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger
                  AFTER INSERT ON build FOR EACH ROW
                  WHEN (NEW.state = 3 OR NEW.state = 5)
                  EXECUTE PROCEDURE update_last_complete_build();
              DROP TRIGGER IF EXISTS update_last_complete_build_trigger_up
                    ON build;
              CREATE TRIGGER update_last_complete_build_trigger_up
                  AFTER UPDATE ON build FOR EACH ROW
                  WHEN (OLD.state != NEW.state)
                  EXECUTE PROCEDURE update_last_complete_build();
               """)

def downgrade():
    raise NotImplementedError()
