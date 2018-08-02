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

"""
This module contains functions wrapping dependency resolution functionality
from hawkey/libdnf.
"""

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
    """
    Perform resolution (simulated installation) of given dependencies and build group.
    The only difference in treatment of dependencies vs. packages from the build group is
    that missing packages in build group are silently skipped, whereas missing packages
    in dependencies are reported as problems and fail the resolution.

    :param sack: hawkey.Sack to use for the resolution.
    :param br: List of dependencies (strings from BuildRequires)
    :param group: list of packages in the build group (strings)
    :return: If the resolution succeeded:
             (True, [], installs), where installs is list of string names of packages
             that would be installed.
             If the resolution failed (something was not installable):
             (False, problems, None), where problems is a list of human-readable strings
             describing the problems that prevent installation.
    """
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
    kwargs = {}
    if get_config('dependency.ignore_weak_deps'):
        kwargs = {'ignore_weak_deps': True}
    goal.run(**kwargs)
    for first, *rest in goal.problem_rules():
        problems.append(
            f"Problem: {first}" +
            ''.join(f'\n - {problem}' for problem in rest)
        )
    resolved = not problems
    return resolved, problems, goal.list_installs() if resolved else None


class DependencyWithDistance(object):
    """
    Object with same fields as Dependency + additional distance field used to represent
    distance from first-level dependencies in the object graph.
    """
    def __init__(self, name, epoch, version, release, arch):
        self.name = name
        self.epoch = epoch
        self.version = version
        self.release = release
        self.arch = arch
        self.distance = None


def compute_dependency_distances(sack, br, deps):
    """
    Computes dependency distance of given dependencies.
    Dependency distance is the length of the shortest path from any of the first-level
    dependencies (BuildRequires) to the dependency node in the dependency graph.
    The algorithm is only a best-effort approximation that uses hawkey queries.
    It is a variant of depth-limited BFS (depth limit is hardcoded to 5).
    Dependency objects are mutated in place. Objects that weren't reached keep their
    original distance (None).

    :param sack: hawkey.Sack used for dependency queries
    :param br: List of BuildRequires -- first-level dependencies. Build group should not
               be included.
    :param deps: List of DependencyWithDistance objects for all dependencies that were
                 marked to be installed.
    """
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
