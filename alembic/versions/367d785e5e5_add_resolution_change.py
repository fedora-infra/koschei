"""
Add resolution_change

Create Date: 2016-08-15 15:59:46.872780

"""

# revision identifiers, used by Alembic.
revision = '367d785e5e5'
down_revision = '408d2abc986'

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE resolution_change (
            id SERIAL PRIMARY KEY,
            package_id integer NOT NULL REFERENCES package(id) ON DELETE CASCADE,
            resolved boolean NOT NULL,
            "timestamp" timestamp without time zone DEFAULT clock_timestamp() NOT NULL
        );
        ALTER TABLE resolution_problem ADD COLUMN resolution_id integer
            REFERENCES resolution_change(id) ON DELETE CASCADE;
        INSERT INTO resolution_change(package_id, resolved)
            SELECT id, resolved FROM package WHERE resolved IS FALSE;
        CREATE INDEX ix_resolution_result_package_id ON resolution_change(package_id);
        UPDATE resolution_problem SET resolution_id=q.id
            FROM (SELECT id, package_id FROM resolution_change) AS q
            WHERE resolution_problem.package_id=q.package_id;
        ALTER TABLE resolution_problem DROP COLUMN package_id;
        ALTER TABLE resolution_problem ALTER COLUMN resolution_id SET NOT NULL;
        CREATE INDEX ix_resolution_problem_resolution_id ON resolution_problem(resolution_id);
    """)


def downgrade():
    op.execute("""
        ALTER TABLE resolution_problem ADD COLUMN package_id integer
            REFERENCES package(id) ON DELETE CASCADE;
        UPDATE resolution_problem SET package_id=q.package_id
            FROM (SELECT DISTINCT ON (package_id) id, package_id
                  FROM resolution_change
                  ORDER BY package_id, "timestamp" DESC) AS q
            WHERE resolution_id=q.id;
        ALTER TABLE resolution_problem ALTER COLUMN package_id SET NOT NULL;
        ALTER TABLE resolution_problem DROP COLUMN resolution_id;
        DROP TABLE resolution_change;
    """)
