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

from flask import url_for
from jinja2 import Markup

from koschei.frontend.base import app
from koschei.models import Package, Build, ResolutionChange


def icon(name, title=None):
    url = url_for('static', filename='images/{}.png'.format(name))
    return Markup(
        '<img src="{url}" title="{title}"/>'
        .format(url=url, title=title or name)
    )


def package_state_icon(package_or_state):
    state_string = getattr(package_or_state, 'state_string', package_or_state)
    icon_name = {
        'ok': 'complete',
        'failing': 'failed',
        'unresolved': 'cross',
        'blocked': 'unknown',
        'untracked': 'unknown'
    }.get(state_string, 'unknown')
    return icon(icon_name, state_string)
Package.state_icon = property(package_state_icon)


def package_running_icon(self):
    if self.has_running_build:
        return icon('running')
    return ''
Package.running_icon = property(package_running_icon)


def resolution_state_icon(resolved):
    if resolved is None:
        return icon('unknown')
    if resolved is True:
        return icon('complete')
    return icon('cross')


def build_state_icon(build_or_state):
    if build_or_state is None:
        return ""
    if isinstance(build_or_state, int):
        state_string = Build.REV_STATE_MAP[build_or_state]
    else:
        state_string = getattr(build_or_state, 'state_string', build_or_state)
    return icon(state_string)
Build.state_icon = property(build_state_icon)


def build_css_class(build):
    css = ''
    if build.untagged:
        css += " kk-untagged-build"
    if build.real:
        css += " table-info"
    if build.state == Build.FAILED:
        css += " table-warning"
    return css
Build.css_class = property(build_css_class)


def resolution_change_css_class(resolution_change):
    if resolution_change.resolved:
        return "table-success"
    return "table-danger"
ResolutionChange.css_class = property(resolution_change_css_class)


app.jinja_env.globals.update(
    # state icon functions
    package_state_icon=package_state_icon,
    build_state_icon=build_state_icon,
    resolution_state_icon=resolution_state_icon,
)
