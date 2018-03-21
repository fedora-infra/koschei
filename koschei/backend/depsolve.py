# Copyright (C) 2014-2018  Red Hat, Inc.
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


class DependencyWithDistance(object):
    def __init__(self, pkg, distance):
        self.name = pkg.name
        self.epoch = pkg.epoch
        self.version = pkg.version
        self.release = pkg.release
        self.arch = pkg.arch
        self.distance = distance


class Solver(object):
    def __init__(self, sack):
        self._sack = sack

    def buildroot(self, build_group):
        return BuildrootSolution(self._sack, build_group)


class BaseSolution(object):
    def __init__(self, sack):
        self._goal = hawkey.Goal(sack)
        self.resolved = False
        self.problems = []

    def _get_builddep_selector(self, dep):
        # Try to find something by provides
        sltr = hawkey.Selector(self._goal.sack)
        sltr.set(provides=dep)
        found = sltr.matches()
        if not found and dep.startswith("/"):
            # Nothing matches by provides and since it's file, try by files
            sltr = hawkey.Selector(self._goal.sack)
            sltr.set(file=dep)
        return sltr

    @property
    def installs(self):
        return set(self._goal.list_installs())


class BuildrootSolution(BaseSolution):
    def __init__(self, sack, build_group):
        super(BuildrootSolution, self).__init__(sack)
        for name in build_group:
            sltr = self._get_builddep_selector(name)
            if sltr.matches():
                # missing packages are silently skipped as in dnf
                self._goal.install(select=sltr)
        kwargs = {}
        if get_config('dependency.ignore_weak_deps'):
            kwargs = {'ignore_weak_deps': True}
        self.resolved = self._goal.run(**kwargs)
        self.problems = self._goal.problems

    def builddep(self, buildrequires):
        assert self.resolved
        return BuilddepSolution(self, buildrequires)


class BuilddepSolution(BaseSolution):
    def __init__(self, buildroot, buildrequires):
        super(BuilddepSolution, self).__init__(buildroot._goal.sack)
        self._roots = set()
        for pkg in buildroot.installs:
            self._goal.install(package=pkg)
        for r in buildrequires:
            sltr = self._get_builddep_selector(r)
            self._roots.update(sltr.matches())
            # pylint: disable=E1103
            if not sltr.matches():
                self.problems.append("No package found for: {}".format(r))
            else:
                self._goal.install(select=sltr)
        if not self.problems:
            kwargs = {}
            if get_config('dependency.ignore_weak_deps'):
                kwargs = {'ignore_weak_deps': True}
            self.resolved = self._goal.run(**kwargs)
            self.problems = self._goal.problems

    def compute_dependency_distances(self, pkgs, max_level=4):
        distances = []
        done = set()
        roots = self._roots
        if False:
            revdepmap = {pkg: set(self._goal.whatrequires_package(pkg)) for pkg in pkgs}
        else:
            revdepmap = {pkg: set(hawkey.Query(self._goal.sack).filter(requires=pkg.provides)) for pkg in pkgs}
        for level in range(1, max_level):
            if not roots:
                break
            distances.extend(DependencyWithDistance(pkg, level) for pkg in roots)
            done = done | roots
            pkgs = pkgs - roots
            roots = {pkg for pkg in pkgs if revdepmap[pkg] & done}
        distances.extend(DependencyWithDistance(pkg, None) for pkg in pkgs)
        return distances
