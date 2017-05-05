"""
Add emulated materialized views

Create Date: 2017-05-05 15:55:12.281127

"""

# revision identifiers, used by Alembic.
revision = '62baf8e175d0'
down_revision = 'be498b0f08fe'

from alembic import op


def upgrade():
    op.execute("""

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

CREATE TABLE scalar_stats (
    refresh_time timestamp without time zone NOT NULL,
    packages integer NOT NULL,
    tracked_packages integer NOT NULL,
    blocked_packages integer NOT NULL,
    builds integer NOT NULL,
    real_builds integer NOT NULL,
    scratch_builds integer NOT NULL,
    PRIMARY KEY (refresh_time, packages, tracked_packages, blocked_packages, builds, real_builds, scratch_builds)
);

INSERT INTO scalar_stats (SELECT now() AS refresh_time, (SELECT count(package.id) AS count_1 
	FROM package) AS packages, (SELECT count(package.id) AS count_2 
	FROM package 
	WHERE package.tracked) AS tracked_packages, (SELECT count(package.id) AS count_3 
	FROM package 
	WHERE package.blocked) AS blocked_packages, (SELECT count(build.id) AS count_4 
	FROM build) AS builds, (SELECT count(build.id) AS count_5 
	FROM build 
	WHERE build.real) AS real_builds, (SELECT count(build.id) AS count_6 
	FROM build 
	WHERE NOT build.real) AS scratch_builds);

CREATE INDEX ix_resource_consumption_stats_total_time ON resource_consumption_stats ("time");

    """)


def downgrade():
    op.execute("""

DROP INDEX ix_resource_consumption_stats_total_time;
DROP TABLE scalar_stats;
DROP TABLE resource_consumption_stats;

    """)
