"""
Reference dependency from depchanges

Create Date: 2017-09-04 11:49:48.305676

"""

# revision identifiers, used by Alembic.
revision = '6d4894cd2307'
down_revision = '0300b36eeaf4'

from alembic import op


def upgrade():
    op.execute("""
        INSERT INTO dependency(name, epoch, version, release, arch)
            SELECT dep_name, prev_epoch, prev_version, prev_release, 'x86_64'
            FROM (
                (
                    SELECT DISTINCT dep_name, prev_epoch, prev_version, prev_release
                        FROM applied_change
                        WHERE prev_version IS NOT NULL
                ) UNION (
                    SELECT DISTINCT dep_name, curr_epoch, curr_version, curr_release
                        FROM applied_change
                        WHERE curr_version IS NOT NULL
                ) EXCEPT (
                    SELECT name, epoch, version, release FROM dependency
                )
            ) as deps;

        CREATE SEQUENCE applied_change2_id_seq;

        CREATE TABLE applied_change2 AS
            SELECT nextval('applied_change2_id_seq') as id,
                build_id,
                (
                    SELECT id FROM dependency
                    WHERE dep_name = name
                      AND prev_version = version
                      AND prev_epoch = epoch
                      AND prev_release = release
                ) AS prev_dep_id,
                (
                    SELECT id FROM dependency
                    WHERE dep_name = name
                      AND curr_version = version
                      AND curr_epoch = epoch
                      AND curr_release = release
                ) AS curr_dep_id,
                distance
            FROM applied_change;

        ALTER TABLE applied_change2
            ADD PRIMARY KEY(id),
            ALTER COLUMN id SET DEFAULT nextval('applied_change2_id_seq'::regclass),
            ALTER COLUMN build_id SET NOT NULL,
            ADD CONSTRAINT applied_change_build_id_fkey
                FOREIGN KEY (build_id)
                REFERENCES build(id) ON DELETE CASCADE,
            ADD CONSTRAINT applied_change_prev_dep_id_fkey
                FOREIGN KEY (prev_dep_id)
                REFERENCES dependency(id),
            ADD CONSTRAINT applied_change_curr_dep_id_fkey
                FOREIGN KEY (curr_dep_id)
                REFERENCES dependency(id),
            ADD CONSTRAINT applied_change_dep_id_check
            CHECK (COALESCE(prev_dep_id, 0) <> COALESCE(curr_dep_id, 0));
        ALTER SEQUENCE applied_change2_id_seq OWNED BY applied_change2.id;

        -- BDR doesn't drop dependent objects, we have to drop them all manually
        DROP INDEX ix_applied_change_dep_name;
        DROP INDEX ix_applied_change_build_id;
        DROP INDEX IF EXISTS ix_applied_change_prev_build_id; -- BDR leftover
        ALTER TABLE applied_change ALTER COLUMN id SET DEFAULT NULL;
        ALTER TABLE applied_change DROP CONSTRAINT IF EXISTS applied_change_pkey;
        ALTER TABLE applied_change DROP CONSTRAINT IF EXISTS applied_change_build_id_fkey;
        DROP SEQUENCE IF EXISTS applied_change_id_seq;

        DROP TABLE applied_change;
        DROP TYPE IF EXISTS applied_change; -- BDR-specific hack
        ALTER TABLE applied_change2 RENAME TO applied_change;
        ALTER INDEX applied_change2_pkey RENAME TO applied_change_pkey;
        ALTER SEQUENCE applied_change2_id_seq RENAME TO applied_change_id_seq;

        CREATE INDEX ix_applied_change_build_id ON applied_change(build_id);
        CREATE INDEX ix_applied_change_prev_dep_id ON applied_change(prev_dep_id);
        CREATE INDEX ix_applied_change_curr_dep_id ON applied_change(curr_dep_id);
    """)


def downgrade():
    op.execute("""
        CREATE TABLE applied_change2 AS
            SELECT change.id AS id,
                   change.build_id AS build_id,
                   COALESCE(prev.name, curr.name) AS dep_name,
                   prev.epoch AS prev_epoch,
                   prev.version AS prev_version,
                   prev.release AS prev_release,
                   curr.epoch AS curr_epoch,
                   curr.version AS curr_version,
                   curr.release AS curr_release,
                   change.distance AS distance
                FROM applied_change AS change
                     LEFT JOIN dependency AS prev ON prev.id = prev_dep_id
                     LEFT JOIN dependency AS curr ON curr.id = curr_dep_id;

        ALTER TABLE applied_change2
            ADD PRIMARY KEY(id),
            ALTER COLUMN id SET DEFAULT nextval('applied_change_id_seq'::regclass),
            ALTER COLUMN build_id SET NOT NULL,
            ADD CONSTRAINT applied_change_build_id_fkey
                FOREIGN KEY (build_id)
                REFERENCES build(id) ON DELETE CASCADE,
            ALTER COLUMN dep_name SET NOT NULL;

        ALTER SEQUENCE applied_change_id_seq OWNED BY applied_change2.id;
        DROP INDEX ix_applied_change_build_id;
        DROP TABLE applied_change;
        DROP TYPE IF EXISTS applied_change; -- BDR-specific hack
        ALTER TABLE applied_change2 RENAME TO applied_change;
        ALTER INDEX applied_change2_pkey RENAME TO applied_change_pkey;

        CREATE INDEX ix_applied_change_build_id ON applied_change(build_id);
        CREATE INDEX ix_applied_change_dep_name ON applied_change(dep_name);
    """)
