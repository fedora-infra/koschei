"""Add package.last_complete_build_state

Revision ID: 2914ccf886ef
Revises: 4cb89cde58f
Create Date: 2015-12-04 18:47:44.367584

"""

# revision identifiers, used by Alembic.
revision = '2914ccf886ef'
down_revision = '4cb89cde58f'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('package', sa.Column('last_complete_build_state', sa.Integer(), nullable=True))
    op.execute("UPDATE package SET last_complete_build_state = (SELECT state FROM build WHERE build.id = last_complete_build_id) ")
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


def downgrade():
    op.drop_column('package', 'last_complete_build_state')
