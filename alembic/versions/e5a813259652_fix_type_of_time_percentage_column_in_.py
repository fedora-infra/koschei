"""
Fix type of time_percentage column in resource_consumption_stats table

Create Date: 2017-05-14 14:59:47.221235

"""

# revision identifiers, used by Alembic.
revision = 'e5a813259652'
down_revision = '62baf8e175d0'

from alembic import op


def upgrade():
    op.execute("""

DROP TABLE resource_consumption_stats;

CREATE TABLE resource_consumption_stats (
    name character varying NOT NULL,
    arch character varying NOT NULL,
    "time" interval NOT NULL,
    time_percentage double precision NOT NULL,
    PRIMARY KEY (name, arch, "time", time_percentage)
);

INSERT INTO resource_consumption_stats (SELECT package.name, koji_task.arch, sum(koji_task.finished - koji_task.started) AS time, CAST(EXTRACT(EPOCH FROM sum(koji_task.finished - koji_task.started)) / (SELECT EXTRACT(EPOCH FROM sum(koji_task.finished - koji_task.started)) AS anon_1 
	FROM koji_task) AS FLOAT) AS time_percentage 
	FROM package JOIN build ON package.id = build.package_id JOIN koji_task ON build.id = koji_task.build_id GROUP BY package.name, koji_task.arch);

CREATE INDEX ix_resource_consumption_stats_total_time ON resource_consumption_stats ("time");

""")


def downgrade():
    op.execute("""

DROP TABLE resource_consumption_stats;

CREATE TABLE resource_consumption_stats (
    name character varying NOT NULL,
    arch character varying NOT NULL,
    "time" interval NOT NULL,
    time_percentage integer NOT NULL,
    PRIMARY KEY (name, arch, "time", time_percentage)
);

INSERT INTO resource_consumption_stats (SELECT package.name, koji_task.arch, sum(koji_task.finished - koji_task.started) AS time, EXTRACT(EPOCH FROM sum(koji_task.finished - koji_task.started)) / (SELECT EXTRACT(EPOCH FROM sum(koji_task.finished - koji_task.started)) AS anon_1 
	FROM koji_task) AS time_percentage 
	FROM package JOIN build ON package.id = build.package_id JOIN koji_task ON build.id = koji_task.build_id GROUP BY package.name, koji_task.arch);

CREATE INDEX ix_resource_consumption_stats_total_time ON resource_consumption_stats ("time");

    """)
