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

import json

from flask import request, Response
from koschei.frontend import app, db
from koschei.models import Package, Collection, Build, get_package_state


@app.route('/api/v1/packages')
def list_packages():
    def prepare_package_entry(entry):
        return {
            'name': entry.name,
            'collection': entry.collection_name,
            'state': get_package_state(
                tracked=entry.tracked,
                blocked=entry.blocked,
                resolved=entry.resolved,
                last_complete_build_state=entry.last_complete_build_state,
            ),
            'last_complete_build': {
                'task_id': entry.build_task_id,
            } if entry.build_task_id is not None else None,
        }

    query = (
        db.query(
            Package.name, Package.collection_id, Package.resolved,
            Package.tracked, Package.blocked, Package.last_complete_build_state,
            Build.task_id.label('build_task_id'),
            Collection.name.label('collection_name'),
        )
        .outerjoin(
            Build,
            (Package.last_complete_build_id == Build.id) & Build.last_complete
        )
        .join(Collection)
        .order_by(Package.name)
    )
    if 'name' in request.args:
        query = query.filter(Package.name.in_(request.args.getlist('name')))
    if 'collection' in request.args:
        query = query.filter(Collection.name.in_(request.args.getlist('collection')))

    return Response(
        json.dumps([prepare_package_entry(p) for p in query]),
        mimetype='application/json',
    )
