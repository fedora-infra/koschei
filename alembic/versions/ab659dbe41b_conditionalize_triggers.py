"""Conditionalize triggers

Revision ID: ab659dbe41b
Revises: 4507ca8c33d4
Create Date: 2014-10-21 15:03:23.811941

"""

# revision identifiers, used by Alembic.
revision = 'ab659dbe41b'
down_revision = '4507ca8c33d4'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
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
