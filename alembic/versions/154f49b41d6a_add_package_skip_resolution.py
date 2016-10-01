"""
Add package.skip_resolution

Create Date: 2016-10-01 17:32:27.630532

"""

# revision identifiers, used by Alembic.
revision = '154f49b41d6a'
down_revision = '367d785e5e5'

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE package ADD COLUMN skip_resolution boolean
            NOT NULL
            DEFAULT FALSE
            CONSTRAINT package_skip_resolution_check
                CHECK (NOT skip_resolution OR resolved IS NULL);
    """)


def downgrade():
    op.execute("""
        ALTER TABLE package DROP COLUMN skip_resolution;
    """)
