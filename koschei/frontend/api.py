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

"""
Basic experimental REST API to get the list of packages with their current states.
"""

from flask import request, Response
from sqlalchemy.sql import literal_column, case
from sqlalchemy.orm import aliased

from koschei.frontend.base import db, app
from koschei.models import BasePackage, Package, Collection, Build


def sql_if(cond, then, else_=None):
    return case([(cond, then)], else_=else_)


@app.route('/api/v1/packages')
def list_packages():
    """
    Return a list of all packages as JSON. Uses Postgres to generate all the JSON in a
    single query.

    Optional query parameters:
    collection: filter by collection name (list, literal match)
    name: filter by package name (list, literal match)

    Response format:
    [
        {
            "name": "foo",
            "collection": "f29",
            "state": "unresolved",
            "last_complete_build": {
                "task_id": 123
            }
        },
        ...
    ]
    """
    query = (
        db.query(
            Package.name.label('name'),
            Collection.name.label('collection'),
            # pylint:disable=no-member
            Package.state_string.label('state'),
            sql_if(
                Build.id != None,
                db.query(Build.task_id.label('task_id'),
                         Build.started.label('time_started'),
                         Build.finished.label('time_finished'),
                         Build.epoch.label('epoch'),
                         Build.version.label('version'),
                         Build.release.label('release'))
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

    return Response(query.json(), mimetype='application/json')


@app.route('/api/v1/collections/diff/<name1>/<name2>')
def diff_collections(name1, name2):
    """
    Compare two collections and return a list of packages with differing states
    packages as JSON. Uses Postgres to generate all the JSON in a single query.

    Response format:
    [
        {
            "name": "foo",
            "state: {
                "f25": "ok",
                "f26": "failing",
            }
        },
        ...
    ]
    """
    Package1 = aliased(Package)
    Package2 = aliased(Package)
    collection1 = db.query(Collection).filter_by(name=name1).first_or_404()
    collection2 = db.query(Collection).filter_by(name=name2).first_or_404()
    query = (
        db.query(
            BasePackage.name.label('name'),
            db.query(
                Package1.state_string.label(collection1.name),
                Package2.state_string.label(collection2.name),
            )
            .correlate(Package1, Package2)
            .as_record().label('state'),
        )
        .join(Package1, Package1.base_id == BasePackage.id)
        .join(Package2, Package2.base_id == BasePackage.id)
        .filter(Package1.state_string != Package2.state_string)
        .filter(Package1.collection_id == collection1.id)
        .filter(Package2.collection_id == collection2.id)
        .order_by(BasePackage.name)
    )

    return Response(query.json(), mimetype='application/json')
