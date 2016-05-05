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

from __future__ import print_function

import re
import hawkey

from koschei.config import get_config


def _get_best_selector(sack, dep):
    # Based on get_best_selector in dnf's subject.py

    def is_glob_pattern(pattern):
        return set(pattern) & set("*[?")

    def first(iterable):
        it = iter(iterable)
        try:
            return next(it)
        except StopIteration:
            return None

    def _nevra_to_selector(sltr, nevra):
        if nevra.name is not None:
            if is_glob_pattern(nevra.name):
                sltr.set(name__glob=nevra.name)
            else:
                sltr.set(name=nevra.name)
        if nevra.version is not None:
            evr = nevra.version
            if nevra.epoch is not None and nevra.epoch > 0:
                evr = "%d:%s" % (nevra.epoch, evr)
            if nevra.release is None:
                sltr.set(version=evr)
            else:
                evr = "%s-%s" % (evr, nevra.release)
                sltr.set(evr=evr)
        if nevra.arch is not None:
            sltr.set(arch=nevra.arch)
        return sltr

    subj = hawkey.Subject(dep)
    sltr = hawkey.Selector(sack)

    nevra = first(subj.nevra_possibilities_real(sack, allow_globs=True))
    if nevra:
        s = _nevra_to_selector(sltr, nevra)
        if len(s.matches()) > 0:
            return s

    # pylint: disable=E1101
    reldep = first(subj.reldep_possibilities_real(sack))
    if reldep:
        dep = str(reldep)
        s = sltr.set(provides=dep)
        if len(s.matches()) > 0:
            return s

    if re.search(r'^\*?/', dep):
        key = "file__glob" if is_glob_pattern(dep) else "file"
        return sltr.set(**{key: dep})

    return sltr


def run_goal(sack, br, group):
    # pylint:disable=E1101
    goal = hawkey.Goal(sack)
    problems = []
    for name in group:
        sltr = _get_best_selector(sack, name)
        # missing packages are silently skipped as in dnf
        goal.install(select=sltr)
    for r in br:
        sltr = _get_best_selector(sack, r)
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


def compute_dependency_distances(sack, br, deps):
    dep_map = {dep.name: dep for dep in deps}
    visited = set()
    level = 1
    # pylint:disable=E1103
    pkgs_on_level = {x for r in br for x in
                     _get_best_selector(sack, r).matches()}
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
