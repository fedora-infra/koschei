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

import koji

from collections import OrderedDict, namedtuple

from sqlalchemy.orm import undefer
from sqlalchemy.sql import insert
from sqlalchemy.exc import IntegrityError

from koschei import util
from koschei.config import get_config
from koschei.backend import koji_util, depsolve
from koschei.backend.service import Service
from koschei.backend.repo_util import KojiRepoDescriptor
from koschei.models import Dependency, Build
from koschei.util import Stopwatch, stopwatch

total_time = Stopwatch("Total repo generation")

DepTuple = namedtuple(
    'DepTuple',
    ['id', 'name', 'epoch', 'version', 'release', 'arch'],
)


class DependencyCache(object):
    def __init__(self, capacity):
        self.capacity = capacity
        self.nevras = {}
        self.ids = OrderedDict()

    def _add(self, dep):
        self.ids[dep.id] = dep
        self.nevras[(dep.name, dep.epoch, dep.version, dep.release,
                     dep.arch)] = dep
        if len(self.ids) > self.capacity:
            self._compact()

    def _access(self, dep):
        del self.ids[dep.id]
        self.ids[dep.id] = dep

    def _compact(self):
        _, victim = self.ids.popitem(last=False)
        # pylint: disable=no-member
        del self.nevras[(victim.name, victim.epoch, victim.version,
                         victim.release, victim.arch)]

    def _get_or_create_nevra(self, db, nevra):
        dep = self.nevras.get(nevra)
        if dep is None:
            dep = db.query(*Dependency.inevra)\
                .filter((Dependency.name == nevra[0]) &
                        (Dependency.epoch == nevra[1]) &
                        (Dependency.version == nevra[2]) &
                        (Dependency.release == nevra[3]) &
                        (Dependency.arch == nevra[4]))\
                .first()
            if dep is None:
                kwds = dict(name=nevra[0], epoch=nevra[1], version=nevra[2],
                            release=nevra[3], arch=nevra[4])
                dep_id = db.execute(insert(Dependency, [kwds],
                                           returning=(Dependency.id,)))\
                    .fetchone().id
                dep = DepTuple(id=dep_id, **kwds)
            self._add(dep)
        else:
            self._access(dep)
        return dep

    def get_or_create_nevra(self, db, nevra):
        try:
            with db.begin_nested():
                return self._get_or_create_nevra(db, nevra)
        except IntegrityError:
            # If there was a concurrent insert, the next query must succeed
            return self._get_or_create_nevra(db, nevra)

    def get_or_create_nevras(self, db, nevras):
        res = []
        for nevra in nevras:
            res.append(self._get_or_create_nevra(db, nevra))
        return res

    @stopwatch(total_time, note='dependency cache')
    def get_by_ids(self, db, ids):
        res = []
        missing = []
        for dep_id in ids:
            dep = self.ids.get(dep_id)
            if dep is None:
                missing.append(dep_id)
            else:
                res.append(dep)
                self._access(dep)
        if missing:
            deps = db.query(*Dependency.inevra).filter(Dependency.id.in_(missing)).all()
            for dep in deps:
                self._add(dep)
                res.append(dep)
        assert res
        return res


class Resolver(Service):
    def __init__(self, session):
        super(Resolver, self).__init__(session)
        capacity = get_config('dependency.dependency_cache_capacity')
        self.dependency_cache = DependencyCache(capacity=capacity)

    def get_build_group(self, collection, repo_id):
        """
        Returns a Koji build group for given collection.
        """
        group = koji_util.get_build_group_cached(
            self.session,
            self.session.koji('secondary'),
            collection.build_tag,
            collection.build_group,
            repo_id,
        )
        return group

    def get_rpm_requires(self, collection, nvras):
        """
        Returns a list of lists of build requires of packages with given
        name-version-release-arch.
        """
        return koji_util.get_rpm_requires_cached(
            self.session,
            self.session.secondary_koji_for(collection),
            nvras,
        )

    @stopwatch(total_time)
    def create_dependency_changes(self, deps1, deps2, **rest):
        """
        Creates an intermediate representation of a dependency change
        (difference) between the two sets. The input format is a list of
        objects with name, epoch, version, release, arch properties.
        The output format is a list of dicts corresponding to UnappliedChange
        table row.

        :param: rest Additional key-value parts to store in the output dicts
        """
        if not deps1 or not deps2:
            # TODO packages with no deps
            return []

        def key(dep):
            return dep.name, dep.epoch, dep.version, dep.release, dep.arch

        def create_change(**values):
            new_change = dict(
                prev_version=None, prev_epoch=None, prev_release=None, prev_arch=None,
                curr_version=None, curr_epoch=None, curr_release=None, curr_arch=None,
            )
            new_change.update(rest)
            new_change.update(values)
            return new_change

        old = util.set_difference(deps1, deps2, key)
        new = util.set_difference(deps2, deps1, key)

        changes = {}
        for dependency in old:
            change = create_change(
                dep_name=dependency.name,
                prev_version=dependency.version, prev_epoch=dependency.epoch,
                prev_release=dependency.release, prev_arch=dependency.arch,
                distance=None,
            )
            changes[dependency.name] = change
        for dependency in new:
            change = (
                changes.get(dependency.name) or
                create_change(dep_name=dependency.name)
            )
            change.update(
                curr_version=dependency.version, curr_epoch=dependency.epoch,
                curr_release=dependency.release, curr_arch=dependency.arch,
                distance=dependency.distance,
            )
            changes[dependency.name] = change
        return list(changes.values()) if changes else []

    @stopwatch(total_time, note='separate thread')
    def resolve_dependencies(self, buildroot, br):
        """
        Does a resolution process to install given buildrequires and build
        group using given sack.

        :returns: A triple of (resolved:bool, problems:[str], installs:[str]).
        """
        deps = None
        solution = buildroot.builddep(br)
        if solution.resolved:
            deps = solution.compute_dependency_distances(solution.installs)
        return solution.resolved, solution.problems, deps

    def get_prev_build_for_comparison(self, build):
        """
        Finds a preceding build of the same package that is suitable to be
        compared against.
        """
        return (
            self.db.query(Build)
            .filter_by(package_id=build.package_id)
            .filter(Build.started < build.started)
            .filter(Build.deps_resolved == True)
            .order_by(Build.started.desc())
            .options(undefer('dependency_keys'))
            .first()
        )

    @stopwatch(total_time)
    def get_build_for_comparison(self, package):
        """
        Returns newest build which should be used for dependency
        comparisons or None if it shouldn't be compared at all
        """
        last_build = package.last_build
        if last_build and last_build.state in Build.FINISHED_STATES:
            if last_build.deps_resolved is True:
                return last_build
            if last_build.deps_resolved is False:
                # unresolved build, skip it
                return self.get_prev_build_for_comparison(last_build)
            # not yet processed builds are not considered
            return None

    def create_repo_descriptor(self, collection, repo_id):
        """
        Prepares a RepoDescriptor object for given collection and repo_id.
        Queries Koji for the repo information.  If the repo is not available
        anymore, returns None.
        """
        valid_repo_states = (koji.REPO_STATES['READY'], koji.REPO_STATES['EXPIRED'])
        koji_session = self.session.secondary_koji_for(collection)

        repo_info = koji_session.repoInfo(repo_id)
        if repo_info.get('state') in valid_repo_states:
            return KojiRepoDescriptor(
                koji_id='secondary' if collection.secondary_mode else 'primary',
                build_tag=repo_info['tag_name'],
                repo_id=repo_id,
            )
