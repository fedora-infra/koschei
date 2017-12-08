# Copyright (C) 2014-2016 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function, absolute_import

import re

import six.moves.urllib as urllib
from flask import request, g
from jinja2 import Markup, escape

from koschei.config import get_config
from koschei.frontend.base import app, db
from koschei.models import AdminNotice, BuildrootProblem


def page_args(clear=False, **kwargs):
    def proc_order(order):
        new_order = []
        for item in order:
            if (item.replace('-', '')
                    not in new_order and '-' + item not in new_order):
                new_order.append(item)
        return ','.join(new_order)
    if 'order_by' in kwargs:
        kwargs['order_by'] = proc_order(kwargs['order_by'])
    # the supposedly unnecessary call to items() is needed
    unfiltered = kwargs if clear else dict(request.args.items(), **kwargs)
    args = {k: v for k, v in unfiltered.items() if v is not None}
    encoded = urllib.parse.urlencode(args)
    return '?' + encoded


def generate_links(package):
    for link_dict in get_config('links'):
        name = link_dict['name']
        url = link_dict['url']
        try:
            for interp in re.findall(r'\{([^}]+)\}', url):
                if not re.match(r'package\.?', interp):
                    raise RuntimeError("Only 'package' variable can be "
                                       "interpolated into link url")
                value = package
                for part in interp.split('.')[1:]:
                    value = getattr(value, part)
                if value is None:
                    raise AttributeError()  # continue the outer loop
                url = url.replace('{' + interp + '}',
                                  escape(urllib.parse.quote_plus(str(value))))
            yield name, url
        except AttributeError:
            continue


def get_global_notices():
    notices = [n.content for n in
               db.query(AdminNotice.content).filter_by(key="global_notice")]
    for collection in g.current_collections:
        if collection.latest_repo_resolved is False:
            problems = db.query(BuildrootProblem)\
                .filter_by(collection_id=collection.id).all()
            notices.append("Base buildroot for {} is not installable. "
                           "Dependency problems:<br/>".format(collection) +
                           '<br/>'.join((p.problem for p in problems)))
    notices = list(map(Markup, notices))
    return notices


def require_login():
    return " " if g.user else ' disabled="true" '


app.jinja_env.globals.update(
    page_args=page_args,
    generate_links=generate_links,
    get_global_notices=get_global_notices,
    require_login=require_login,
)
