# Copyright (C) 2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from flask import request, Response
from sqlalchemy.sql import literal_column, case
from koschei.frontend import app, db
from koschei.models import Package, Collection, Build


def sql_if(cond, then, else_=None):
    return case([(cond, then)], else_=else_)


@app.route('/api/v1/packages')
def list_packages():
    query = (
        db.query(
            Package.name.label('name'),
            Collection.name.label('collection'),
            Package.state_string_expression.label('state'),
            sql_if(
                Build.id != None,
                db.query(Build.task_id.label('task_id'))
                .correlate(Build)
                .as_record()
            ).label('last_complete_build')
        )
        .join(Collection)
        .outerjoin(
            Build,
            (Package.last_complete_build_id == Build.id) & Build.last_complete
        )
        .order_by(Package.name)
    )
    if 'name' in request.args:
        query = query.filter(Package.name.in_(request.args.getlist('name')))
    if 'collection' in request.args:
        query = query.filter(Collection.name.in_(request.args.getlist('collection')))

    result = (
        db.query(literal_column(
            "coalesce(array_to_json(array_agg(row_to_json(pkg_query)))::text, '[]')"
        ).label('q'))
        .select_from(query.subquery('pkg_query'))
        .scalar()
    )

    return Response(result, mimetype='application/json')
