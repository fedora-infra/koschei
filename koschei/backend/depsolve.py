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
    br_matches = set()
    for name in group:
        sltr = _get_builddep_selector(sack, name)
        if sltr.matches():
            # missing packages are silently skipped as in dnf
            goal.install(select=sltr)
    for r in br:
        sltr = _get_builddep_selector(sack, r)
        br_matches.update(sltr.matches())
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
        return resolved, goal.problems, set(goal.list_installs()) if resolved else None, br_matches, goal
    return False, problems, None, None, None


class DependencyWithDistance(object):
    def __init__(self, pkg, distance):
        self.name = pkg.name
        self.epoch = pkg.epoch
        self.version = pkg.version
        self.release = pkg.release
        self.arch = pkg.arch
        self.distance = distance


def compute_dependency_distances(goal, pkgs, roots, max_level=4):
    distances = []
    done = set()
    revdepmap = {pkg: set(goal.whatrequires_package(pkg)) for pkg in pkgs}
    for level in range(1, max_level):
        if not roots:
            break
        distances.extend(DependencyWithDistance(pkg, level) for pkg in roots)
        done = done | roots
        pkgs = pkgs - roots
        roots = {pkg for pkg in pkgs if revdepmap[pkg] & done}
    distances.extend(DependencyWithDistance(pkg, None) for pkg in pkgs)
    return distances
