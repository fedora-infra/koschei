"""Store dependencies as compressed many-to-many

Revision ID: 18d4f8beaba6
Revises: 2ffebbebb9e5
Create Date: 2016-02-21 20:23:40.722537

"""

# revision identifiers, used by Alembic.
revision = '18d4f8beaba6'
down_revision = '2ffebbebb9e5'

import struct, zlib
from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator
from sqlalchemy.dialects.postgresql import BYTEA, ARRAY
from sqlalchemy.sql import bindparam

class CompressedKeyArray(TypeDecorator):
    impl = BYTEA

    def process_bind_param(self, value, _):
        if value is None:
            return None
        offset = 0
        for i in range(len(value)):
            value[i] -= offset
            offset += value[i]
        array = bytearray()
        for item in value:
            array += struct.pack(">I", item)
        return zlib.compress(str(array))

    def process_result_value(self, value, _):
        if value is None:
            return None
        res = []
        uncompressed = zlib.decompress(value)
        for i in range(0, len(uncompressed), 4):
            res.append(struct.unpack(">I", str(uncompressed[i:i + 4]))[0])
        offset = 0
        for i in range(len(res)):
            res[i] += offset
            offset = res[i]
        return res



def upgrade():
    op.execute("""\
    CREATE INDEX tmp ON dependency(name,epoch,version,release,arch);
    CREATE TABLE deptmp AS SELECT DISTINCT build_id, min(id) OVER (PARTITION BY name, epoch, version, release, arch) AS newid FROM dependency;
    ALTER TABLE build ADD COLUMN dependency_array integer[];
    UPDATE build SET dependency_array = a FROM (SELECT build_id, array_agg(newid ORDER BY newid) AS a FROM deptmp GROUP BY build_id) AS q WHERE q.build_id = build.id;
    DROP INDEX tmp;
    DROP INDEX ix_dependency_build_id;
    CREATE TABLE deptmp2 AS SELECT DISTINCT newid FROM deptmp;
    DELETE FROM dependency WHERE NOT EXISTS (SELECT 1 FROM deptmp2 WHERE deptmp2.newid=dependency.id);
    ALTER TABLE dependency DROP COLUMN build_id;
    ALTER TABLE dependency DROP COLUMN distance;
    DROP TABLE deptmp;
    DROP TABLE deptmp2;
    ALTER TABLE build ADD COLUMN dependency_keys bytea;
    CREATE UNIQUE INDEX ix_dependency_composite ON dependency(name, epoch, version, release, arch);
    """)
    connection = op.get_bind()
    build_table = sa.Table('build', sa.MetaData(),
                           sa.Column('id', sa.Integer, primary_key=True),
                           sa.Column('dependency_array', ARRAY(sa.Integer)),
                           sa.Column('dependency_keys', CompressedKeyArray))
    updated = []

    def persist():
        connection.execute(build_table.update().where(build_table.c.id == bindparam('i'))\
                           .values({'id': bindparam('i'), 'dependency_keys': bindparam('d')}),
                           updated)
        updated[:] = []

    for row in connection.execution_options(stream_results=True)\
            .execute("SELECT id, dependency_array FROM build WHERE dependency_array IS NOT NULL"):
        updated.append({'i': row.id, 'd': row.dependency_array})
        if len(updated) > 1000:
            persist()
    if updated:
        persist()
    op.execute("ALTER TABLE build DROP COLUMN dependency_array")


def downgrade():
    raise NotImplementedError()
