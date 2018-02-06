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

from __future__ import print_function, absolute_import, division

import koji

from collections import OrderedDict, namedtuple
from six.moves import zip as izip

from sqlalchemy.orm import undefer
from sqlalchemy.sql import insert

from koschei import util
from koschei.config import get_config
from koschei.backend import koji_util, depsolve
from koschei.backend.service import Service
from koschei.backend.koji_util import itercall
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

    def get_or_create_nevra(self, db, nevra):
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

    def get_or_create_nevras(self, db, nevras):
        res = []
        for nevra in nevras:
            res.append(self.get_or_create_nevra(db, nevra))
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

    def get_build_group(self, collection):
        group = koji_util.get_build_group_cached(
            self.session,
            self.session.koji('primary'),
            collection.build_tag,
            collection.build_group,
        )
        return group

    @stopwatch(total_time)
    def create_dependency_changes(self, deps1, deps2, **rest):
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
    def resolve_dependencies(self, sack, br, build_group):
        deps = None
        resolved, problems, installs = depsolve.run_goal(sack, br, build_group)
        if resolved:
            problems = []
            deps = [
                depsolve.DependencyWithDistance(
                    name=pkg.name, epoch=pkg.epoch, version=pkg.version,
                    release=pkg.release, arch=pkg.arch,
                ) for pkg in installs if pkg.arch != 'src'
            ]
            depsolve.compute_dependency_distances(sack, br, deps)
        return resolved, problems, deps

    def get_prev_build_for_comparison(self, build):
        return self.db.query(Build)\
            .filter_by(package_id=build.package_id)\
            .filter(Build.started < build.started)\
            .filter(Build.deps_resolved == True)\
            .order_by(Build.started.desc())\
            .options(undefer('dependency_keys'))\
            .first()

    def set_descriptor_tags(self, collection, descriptors):
        def koji_call(koji_session, desc):
            koji_session.repoInfo(desc.repo_id)

        result_gen = itercall(self.session.secondary_koji_for(collection),
                              descriptors, koji_call)
        for descriptor, repo_info in izip(descriptors, result_gen):
            if repo_info['state'] in (koji.REPO_STATES['READY'],
                                      koji.REPO_STATES['EXPIRED']):
                descriptor.build_tag = repo_info['tag_name']
            else:
                self.log.info('Repo {} is dead, skipping'.format(descriptor.repo_id))

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

    @staticmethod
    def create_repo_descriptor(secondary_mode, repo_id):
        return KojiRepoDescriptor('secondary' if secondary_mode else 'primary',
                                  None, repo_id)
