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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import hawkey

from koschei.config import get_config


def _get_builddep_selector(sack, dep):
    # Try to find something by provides
    sltr = hawkey.Selector(sack)
    sltr.set(provides=dep)
    found = sltr.matches()
    if not found and dep.startswith("/"):
        # Nothing matches by provides and since it's file, try by files
        sltr = hawkey.Selector(sack)
        sltr.set(file=dep)
    return sltr


def run_goal(sack, br, group):
    # pylint:disable=E1101
    goal = hawkey.Goal(sack)
    problems = []
    for name in group:
        sltr = _get_builddep_selector(sack, name)
        if sltr.matches():
            # missing packages are silently skipped as in dnf
            goal.install(select=sltr)
    for r in br:
        sltr = _get_builddep_selector(sack, r)
        # pylint: disable=E1103
        if not sltr.matches():
            problems.append("No package found for: {}".format(r))
        else:
            goal.install(select=sltr)
    if not problems:
        kwargs = {}
        if get_config('dependency.ignore_weak_deps'):
            kwargs = {'ignore_weak_deps': True}
        resolved = goal.run(**kwargs)
        return resolved, goal.problems, goal.list_installs() if resolved else None
    return False, problems, None


class DependencyWithDistance(object):
    def __init__(self, name, epoch, version, release, arch):
        self.name = name
        self.epoch = epoch
        self.version = version
        self.release = release
        self.arch = arch
        self.distance = None


def compute_dependency_distances(sack, br, deps):
    dep_map = {dep.name: dep for dep in deps}
    visited = set()
    level = 1
    # pylint:disable=E1103
    pkgs_on_level = {x for r in br for x in
                     _get_builddep_selector(sack, r).matches()}
    while pkgs_on_level:
        for pkg in pkgs_on_level:
            dep = dep_map.get(pkg.name)
            if dep and dep.distance is None:
                dep.distance = level
        level += 1
        if level >= 5:
            break
        reldeps = {req for pkg in pkgs_on_level if pkg not in visited
                   for req in pkg.requires}
        visited.update(pkgs_on_level)
        pkgs_on_level = set(hawkey.Query(sack).filter(provides=reldeps))
