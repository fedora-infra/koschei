"""
Normalize UnappliedChange

Create Date: 2018-05-04 13:40:02.251000

"""

# revision identifiers, used by Alembic.
revision = '8ab06d9c83e7'
down_revision = '0337520adb1e'

from alembic import op


def upgrade():
    # UnappliedChange is transient data, it can be deleted and it will be
    # regenerated on next resolver run
    op.execute("""
        DELETE FROM unapplied_change;
        ALTER TABLE unapplied_change DROP COLUMN dep_name;
        ALTER TABLE unapplied_change DROP COLUMN prev_epoch;
        ALTER TABLE unapplied_change DROP COLUMN prev_version;
        ALTER TABLE unapplied_change DROP COLUMN prev_release;
        ALTER TABLE unapplied_change DROP COLUMN curr_epoch;
        ALTER TABLE unapplied_change DROP COLUMN curr_version;
        ALTER TABLE unapplied_change DROP COLUMN curr_release;
        ALTER TABLE unapplied_change ADD COLUMN prev_dep_id integer
            REFERENCES dependency(id);
        ALTER TABLE unapplied_change ADD COLUMN curr_dep_id integer
            REFERENCES dependency(id);
        ALTER TABLE unapplied_change ADD CONSTRAINT unapplied_change_dep_id_check
            CHECK ((COALESCE(prev_dep_id, 0) <> COALESCE(curr_dep_id, 0)));
        CREATE INDEX ix_unapplied_change_prev_dep_id ON unapplied_change(prev_dep_id);
        CREATE INDEX ix_unapplied_change_curr_dep_id ON unapplied_change(curr_dep_id);
    """)


def downgrade():
    op.execute("""
        DELETE FROM unapplied_change;
        ALTER TABLE unapplied_change DROP CONSTRAINT unapplied_change_dep_id_check;
        ALTER TABLE unapplied_change DROP COLUMN prev_dep_id;
        ALTER TABLE unapplied_change DROP COLUMN curr_dep_id;
        ALTER TABLE unapplied_change ADD COLUMN dep_name character varying NOT NULL;
        ALTER TABLE unapplied_change ADD COLUMN prev_epoch integer;
        ALTER TABLE unapplied_change ADD COLUMN prev_version character varying;
        ALTER TABLE unapplied_change ADD COLUMN prev_release character varying;
        ALTER TABLE unapplied_change ADD COLUMN curr_epoch integer;
        ALTER TABLE unapplied_change ADD COLUMN curr_version character varying;
        ALTER TABLE unapplied_change ADD COLUMN curr_release character varying;
    """)
