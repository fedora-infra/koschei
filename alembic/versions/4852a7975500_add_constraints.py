"""Add constraints

Revision ID: 4852a7975500
Revises: 13f40f47ff77
Create Date: 2016-05-18 18:54:06.058326

"""

# revision identifiers, used by Alembic.
revision = '4852a7975500'
down_revision = '13f40f47ff77'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("""
               ALTER TABLE koji_task ADD CONSTRAINT koji_task_state_check CHECK (state BETWEEN 0 AND 5);
               ALTER TABLE koji_task ALTER COLUMN arch TYPE character varying;
               ALTER TABLE koji_task ALTER COLUMN arch SET NOT NULL;

               ALTER TABLE build ADD CONSTRAINT build_state_check CHECK (state IN (2, 3, 5));
               ALTER TABLE build ADD CONSTRAINT build_repo_id_check CHECK (state = 2 OR repo_id IS NOT NULL);
               ALTER TABLE build ADD CONSTRAINT build_version_check CHECK (state = 2 OR version IS NOT NULL);
               ALTER TABLE build ADD CONSTRAINT build_release_check CHECK (state = 2 OR release IS NOT NULL);
               ALTER TABLE build ADD CONSTRAINT build_real_complete_check CHECK (NOT real OR state <> 2);
               ALTER TABLE build ALTER COLUMN task_id SET NOT NULL;
               """)

def downgrade():
    op.execute("""
               ALTER TABLE koji_task DROP CONSTRAINT koji_task_state_check;
               ALTER TABLE koji_task ALTER COLUMN arch TYPE character varying(16);
               ALTER TABLE koji_task ALTER COLUMN arch DROP NOT NULL;

               ALTER TABLE build DROP CONSTRAINT build_state_check;
               ALTER TABLE build DROP CONSTRAINT build_repo_id_check;
               ALTER TABLE build DROP CONSTRAINT build_version_check;
               ALTER TABLE build DROP CONSTRAINT build_release_check;
               ALTER TABLE build DROP CONSTRAINT build_real_complete_check;
               ALTER TABLE build ALTER COLUMN task_id DROP NOT NULL;
               """)
