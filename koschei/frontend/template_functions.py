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

"""
A collection of functions used in jinja2 templates as global functions.
"""

import re

from urllib.parse import urlencode, quote_plus

from flask import request, g
from markupsafe import Markup, escape

from koschei.config import get_config
from koschei.frontend.base import app, db
from koschei.models import AdminNotice, BuildrootProblem


def page_args(clear=False, **kwargs):
    """
    Produces a query string for a URL while special-casing some of the arguments.
    Retains current query arguments (for current request), unless clean is specified.

    :param clear: Whether to clear current query arguments (passed to current request)
    :param kwargs: Arguments converted to query arguments (urlencoded).
                   order_by argument is special-cased as it accepts a list of order names
                   and is converted to string representation first (removing redundant
                   entries in the process).
    :return:
    """
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
    encoded = urlencode(args)
    return '?' + encoded


def generate_links(package):
    """
    Generate list of links to a package in related applications (taken from config).

    :param package: Package for which to generate links
    :return: generates (name, url) tuples. name is the application name
    """
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
                                  escape(quote_plus(str(value))))
            yield name, url
        except AttributeError:
            continue


def get_global_notices():
    """
    Constructs a list of HTML elements representing current global notices taken from the
    DB table AdminNotice and also adds a warning if the base buildroot is unresolved.

    :return: List of directly renderable items. May be empty.
    """
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
    """
    Used to disable elements for non-logged-in sessions.
    """
    return " " if g.user else ' disabled="true" '


__key_counter = 0


def next_key():
    """
    Get a next unique integer from global sequence. Used to generate IDs of elements.
    :return: integer id
    """
    global __key_counter
    __key_counter += 1
    return __key_counter


app.jinja_env.globals.update(
    page_args=page_args,
    generate_links=generate_links,
    get_global_notices=get_global_notices,
    require_login=require_login,
    next_key=next_key,
)
