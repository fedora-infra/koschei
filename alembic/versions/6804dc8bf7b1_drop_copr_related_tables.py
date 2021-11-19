"""
Drop Copr-related tables

Create Date: 2021-11-19 15:16:16.442925

"""

# revision identifiers, used by Alembic.
revision = '6804dc8bf7b1'
down_revision = 'f97587df0763'

from alembic import op


def upgrade():
    op.execute("""
        DROP TABLE copr_rebuild;
        DROP TABLE copr_resolution_change;
        DROP TABLE copr_rebuild_request;
        DROP TYPE rebuild_request_state;
    """)


def downgrade():
    op.execute("""
CREATE TYPE rebuild_request_state AS ENUM (
    'new',
    'in progress',
    'scheduled',
    'finished',
    'failed'
);

CREATE TABLE copr_rebuild_request (
    id serial PRIMARY KEY,
    user_id integer NOT NULL,
    collection_id integer NOT NULL,
    repo_source character varying NOT NULL,
    yum_repo character varying,
    "timestamp" timestamp without time zone DEFAULT clock_timestamp() NOT NULL,
    description character varying,
    repo_id integer,
    schedule_count integer,
    scheduler_queue_index integer,
    state rebuild_request_state NOT NULL DEFAULT 'new',
    error character varying
);

CREATE TABLE copr_resolution_change (
    request_id integer NOT NULL REFERENCES copr_rebuild_request(id) ON DELETE CASCADE,
    package_id integer NOT NULL REFERENCES package(id) ON DELETE CASCADE,
    prev_resolved boolean NOT NULL,
    curr_resolved boolean NOT NULL,
    problems character varying[],
    PRIMARY KEY (request_id, package_id)
);

CREATE TABLE copr_rebuild (
    request_id integer NOT NULL REFERENCES copr_rebuild_request(id) ON DELETE CASCADE,
    package_id integer NOT NULL REFERENCES package(id) ON DELETE CASCADE,
    copr_build_id integer,
    prev_state integer NOT NULL,
    state integer,
    "order" integer NOT NULL,
    approved boolean,
    running boolean NOT NULL DEFAULT false,
    PRIMARY KEY (request_id, package_id)
);
    """)
