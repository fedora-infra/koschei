# Copyright (C) 2014  Red Hat, Inc.
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

from flask import Flask, abort, request
from flask_sqlalchemy import BaseQuery, Pagination
from sqlalchemy.orm import scoped_session, sessionmaker

from koschei.util import config
from koschei.models import engine

dirs = config['directories']
app = Flask('koschei', template_folder=dirs['templates'],
            static_folder=dirs['static_folder'],
            static_url_path=dirs['static_url'])
app.config.update(config['flask'])

frontend_config = config['frontend']
items_per_page = frontend_config['items_per_page']


def paginate(self):
    page = int(request.args.get('page', 1))
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

BaseQuery.paginate = paginate

db = scoped_session(sessionmaker(autocommit=False, bind=engine,
                                 query_cls=BaseQuery))

# Following will make pylint shut up about missing query method
if False:
    db.query = lambda *args: None
    db.add = lambda x: None
    db.commit = lambda: None
    db.flush = lambda: None
