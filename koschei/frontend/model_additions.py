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
This module monkey-patches additional properties/methods to model objects
and adds few model specific global functions to templates.
"""

import re

from flask import url_for, escape
from markupsafe import Markup

from koschei.frontend.base import app
from koschei.models import (
    Package, Build, ResolutionChange, AppliedChange, UnappliedChange, ResolutionProblem,
)


def icon(name, title=None):
    """
    Get `img` tag for an icon.
    """
    url = url_for('static', filename='images/{}.png'.format(name))
    return Markup(
        '<img src="{url}" title="{title}"/>'
        .format(url=url, title=title or name)
    )


def package_state_icon(package_or_state):
    """
    Get `img` tag of an icon corresponding to package's state.

    Available as `state_icon` method on Package.

    :param package_or_state: package ORM object or state string
    :return: directly renderable object
    """
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
    """
    Get `img` tag of an icon corresponding to package's running icon if it has a running
    build.

    Available as `running_icon` method on Package.

    :return: directly renderable object
    """
    if self.has_running_build:
        return icon('running')
    return ''


Package.running_icon = property(package_running_icon)


def resolution_state_icon(resolved):
    """
    Get `img` tag of an icon corresponding to resolution state.

    :return: directly renderable object
    """
    if resolved is None:
        return icon('unknown')
    if resolved is True:
        return icon('complete')
    return icon('cross')


def build_state_icon(build_or_state):
    """
    Get `img` tag of an icon corresponding to build's state.

    Available as `state_icon` method on Build.

    :param build_or_state: build ORM object or state string
    :return: directly renderable object
    """
    if build_or_state is None:
        return ""
    if isinstance(build_or_state, int):
        state_string = Build.REV_STATE_MAP[build_or_state]
    else:
        state_string = getattr(build_or_state, 'state_string', build_or_state)
    return icon(state_string)


Build.state_icon = property(build_state_icon)


def build_css_class(build):
    """
    Get CSS class for a build row, determined by build's properties.

    Available as `css_class` property of Build.

    :return: CSS class string
    """
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
    """
    Get CSS class for a resolution change row, determined by its properties.

    Available as `css_class` property of ResolutionChange.

    :return: CSS class string
    """
    if resolution_change.resolved:
        return "table-success"
    return "table-danger"


ResolutionChange.css_class = property(resolution_change_css_class)


def problem_html(self):
    return str(escape(str(self))).replace('\n', '<br>')


ResolutionProblem.__html__ = problem_html


EVR_SEPARATORS_RE = re.compile(r'([-._~])')


def dependency_change_pretty_evrs(change):
    """
    Convert a dependency change to a form with highlighted changed version components.

    Available as `pretty_evrs` method or both AppliedChange and UnappliedChange.

    :return: a tuple of (prev, curr), where both are directly renderable representations
             of the previous and current state.
    """
    s1, s2 = [
        EVR_SEPARATORS_RE.split(str(evr)) if evr else []
        for evr in (change.prev_evr, change.curr_evr)
    ]
    for i in range(min(len(s1), len(s2))):
        if s1[i] != s2[i]:
            s1[i] = f'<span class="kk-evr-diff">{s1[i]}</span>'
            s2[i] = f'<span class="kk-evr-diff">{s2[i]}</span>'
            break
    return Markup(''.join(s1)), Markup(''.join(s2))


AppliedChange.pretty_evrs = property(dependency_change_pretty_evrs)
UnappliedChange.pretty_evrs = property(dependency_change_pretty_evrs)


app.jinja_env.globals.update(
    # state icon functions
    package_state_icon=package_state_icon,
    build_state_icon=build_state_icon,
    resolution_state_icon=resolution_state_icon,
)
