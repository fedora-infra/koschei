# Copyright (C) 2014-2016  Red Hat, Inc.
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
# Author: Michael Simacek <msimacek@redhat.com>

"""
Module responsible for setting the basic environment for the frontend and creating
global resources, such as the Flask app or the database.
"""

import logging

from flask import Flask, abort, request, g
from flask_sqlalchemy import BaseQuery, Pagination
from sqlalchemy.orm import scoped_session, sessionmaker

from koschei.config import get_config
from koschei.db import Query, get_engine
from koschei.models import LogEntry, Collection, Package, Build, ResolutionChange
from koschei.session import KoscheiSession

dirs = get_config('directories')
app = Flask('koschei', template_folder=dirs['templates'],
            static_folder=dirs['static_folder'],
            static_url_path=dirs['static_url'])
app.config.update(get_config('flask'))

frontend_config = get_config('frontend')


class FrontendQuery(Query, BaseQuery):
    """
    Custom query subclass of Flask-SQLAlchemy's BaseQuery extended with custom pagination
    wrapper.
    """
    # pylint:disable=arguments-differ
    def paginate(self, items_per_page):
        """
        Sets up pagination based on request query arguments.
        Raises 404 is the page is out-of-bounds.

        :return: Flask-SQLAlchemy's Pagination wrapper.
        """
        try:
            page = int(request.args.get('page', 1))
        except ValueError:
            abort(400)
        if page < 1:
            abort(404)
        items = self.limit(items_per_page)\
                    .offset((page - 1) * items_per_page).all()
        if not items and page != 1:
            abort(404)
        if page == 1 and len(items) < items_per_page:
            total = len(items)
        else:
            total = self.order_by(None).count()
        return Pagination(self, page, items_per_page, total, items)


# Thread-local database session
db = scoped_session(sessionmaker(autocommit=False, bind=get_engine(),
                                 query_cls=FrontendQuery))


class KoscheiFrontendSession(KoscheiSession):
    """
    KoscheiSession with frontend-specific additions.
    """
    db = db
    log = logging.getLogger('koschei.frontend')

    def log_user_action(self, message, **kwargs):
        self.db.add(
            LogEntry(environment='frontend', user=g.user, message=message, **kwargs),
        )


# Global KoscheiSession. The db in it is thread-local and is the same object as global db
session = KoscheiFrontendSession()


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.remove()


@app.context_processor
def inject_fedmenu():
    # TODO move to global vars
    if 'fedmenu_url' in frontend_config:
        return {
            'fedmenu_url': frontend_config['fedmenu_url'],
            'fedmenu_data_url': frontend_config['fedmenu_data_url'],
        }
    return {}


@app.before_request
def get_collections():
    """
    Sets up current_collections and collections global variables on Flask's `g` object.
    current_collections is a list of collections specified by query arguments or
                        a list of all collections.
    all_collections is a list of all collections in the DB.
    :return:
    """
    if request.endpoint == 'static':
        return
    collection_name = request.args.get('collection')
    g.collections = db.query(Collection)\
        .order_by(Collection.order.desc(), Collection.name.desc())\
        .all()
    for collection in g.collections:
        db.expunge(collection)
    if not g.collections:
        abort(500, "No collections setup")
    g.collections_by_name = {c.name: c for c in g.collections}
    g.collections_by_id = {c.id: c for c in g.collections}
    g.current_collections = []
    if collection_name:
        try:
            for component in collection_name.split(','):
                g.current_collections.append(g.collections_by_name[component])
        except KeyError:
            abort(404, "Collection not found")
    else:
        g.current_collections = g.collections


def secondary_koji_url(collection):
    """
    Return secondary Koji (the read-only one) web URL for given collection.
    For collections in primary mode, it returns URL of primary Koji.
    """
    if collection.secondary_mode:
        return get_config('secondary_koji_config.weburl')
    return get_config('koji_config.weburl')


app.jinja_env.globals.update(
    # configuration variables
    koschei_version=get_config('version'),
    primary_koji_url=get_config('koji_config.weburl'),
    secondary_koji_url=secondary_koji_url,
    fedora_assets_url=frontend_config['fedora_assets_url'],
    # builtin python functions
    inext=next, iter=iter, min=min, max=max,
    # model classes
    Package=Package,
    Build=Build,
    ResolutionChange=ResolutionChange,
)
