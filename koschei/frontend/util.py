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

from __future__ import print_function, absolute_import

from flask import abort, flash


def flash_ack(message):
    """Send flask flash with message about operation that was successfully
       completed."""
    flash(message, 'success')


def flash_nak(message):
    """Send flask flash with with message about operation that was not
       completed due to an error."""
    flash(message, 'danger')


def flash_info(message):
    """Send flask flash with informational message."""
    flash(message, 'info')


class Reversed(object):
    def __init__(self, content):
        self.content = content

    def desc(self):
        return self.content

    def asc(self):
        return self.content.desc()


class NullsLastOrder(Reversed):
    def asc(self):
        return self.content.desc().nullslast()


def get_order(order_map, order_spec):
    orders = []
    components = order_spec.split(',')
    for component in components:
        if component:
            if component.startswith('-'):
                order = [o.desc() for o in order_map.get(component[1:], ())]
            else:
                order = [o.asc() for o in order_map.get(component, ())]
            orders.extend(order)
    if any(order is None for order in orders):
        abort(400)
    return components, orders
