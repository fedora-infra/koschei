"""Cascading deletes

Revision ID: 155bedecdab
Revises: 4c071375b510
Create Date: 2014-08-15 19:27:27.917122

"""

# revision identifiers, used by Alembic.
revision = '155bedecdab'
down_revision = '4c071375b510'

from alembic import op
import sqlalchemy as sa

#from koschei.models import *

def add_cascade(column, drop=True):
    src_table = column.class_.__tablename__
    src_col = column.key
    [fkey] = column.class_.__table__.c[src_col].foreign_keys
    dst_table, dst_col = fkey._get_colspec().split('.')
    fkey_name = '{}_{}_fkey'.format(src_table, src_col)
    if drop:
        op.drop_constraint(fkey_name, src_table)
    op.create_foreign_key(fkey_name, src_table, dst_table,
                          [src_col], [dst_col], ondelete='CASCADE')

def upgrade():
    add_cascade(Build.package_id)
    add_cascade(BuildrootDiff.prev_build_id)
    add_cascade(BuildrootDiff.curr_build_id)
    add_cascade(ResolutionResult.repo_id, drop=False)
    add_cascade(ResolutionResult.package_id)
    add_cascade(ResolutionProblem.resolution_id)
    add_cascade(Dependency.repo_id)
    add_cascade(Dependency.package_id)
    c = DependencyChange.__table__
    b = Build.__table__
    op.execute(c.delete().where(c.c.applied_in_id.notin_(select([b.c.id]))))
    add_cascade(DependencyChange.applied_in_id, drop=False)
    add_cascade(DependencyChange.package_id)
    add_cascade(PackageGroupRelation.group_id)
    add_cascade(PackageGroupRelation.package_id)

def downgrade():
    raise NotImplementedError()
