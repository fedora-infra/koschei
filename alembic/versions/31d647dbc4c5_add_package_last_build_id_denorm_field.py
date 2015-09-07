"""Add package.last_build_id denorm field

Revision ID: 31d647dbc4c5
Revises: 4feeaaf796ad
Create Date: 2015-09-07 15:02:26.913895

"""

# revision identifiers, used by Alembic.
revision = '31d647dbc4c5'
down_revision = '4feeaaf796ad'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('package', sa.Column('last_build_id', sa.Integer(), nullable=True))
    # There was mismatch between code and db about the naming, let's redo it
    op.execute("""
               ALTER TABLE package DROP CONSTRAINT IF EXISTS fkey_package_last_complete_build_id;
               ALTER TABLE package DROP CONSTRAINT IF EXISTS fkey_package_build_id;
               """)
    op.create_foreign_key('fkey_package_last_build_id', 'package', 'build',
                          ['last_build_id'], ['id'])
    op.create_foreign_key('fkey_package_last_complete_build_id', 'package', 'build',
                          ['last_complete_build_id'], ['id'])

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
               """)

    op.execute("""
               UPDATE package SET last_build_id = lb.id
               FROM (SELECT DISTINCT ON(package.id) build.id, build.package_id
                     FROM package JOIN build
                     ON package.id = build.package_id
                     ORDER BY package.id, build.id DESC) AS lb
               WHERE package.id = lb.package_id;
               """)

def downgrade():
    raise NotImplementedError()
