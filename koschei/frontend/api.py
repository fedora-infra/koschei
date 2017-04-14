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

from flask import jsonify, request, make_response
from sqlalchemy.orm import joinedload
from koschei.frontend import app, db, frontend_config, auth, session, forms, Tab
from koschei.models import BasePackage, Package, Collection, Build, KojiTask


def build_to_json(build):
    if build:
        return {'task_id': build.task_id}


def package_to_json(package):
    if package:
        return {'name': package.name,
                  'collection': package.collection.name,
                  'state': package.state_string,
                  'last_complete_build': build_to_json(package.last_complete_build)}


def packages_to_json(packages):
    return [package_to_json(p) for p in packages]


@app.route('/api/v1/packages')
def list_packages():
    packages = db.query(Package)\
        .options(joinedload(Package.collection))\
        .options(joinedload(Package.last_complete_build))\
        .order_by(Package.name).all()
    return jsonify(packages_to_json(packages))
