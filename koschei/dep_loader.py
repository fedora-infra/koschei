# Copyright (C) 2014  Red Hat, Inc.
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

import subprocess

from koschei.models import config, Package, Dependency, Session
from koschei.plugins import Plugin

def get_repoquery_invocation():
    repos = config['repos']
    base_invocation = ['repoquery', '--qf=%{base_package_name}']
    for i, repo in enumerate(repos):
        repoid = 'ci-repo-{}'.format(i)
        base_invocation.append('--repofrompath={},{}'.format(repoid, repo))
        base_invocation.append('--repoid={}'.format(repoid))
    return base_invocation

class DepLoader(Plugin):
    def __init__(self):
        super(DepLoader, self).__init__()
        self.register_event('add_package', self.process_new_package)

    def process_new_package(self, session, pkg):
        if pkg.watched:
            self.process_build_requires(session, pkg)
        self.process_requires(session, pkg)

    def refresh_repo(self):
        session = Session()
        try:
            packages = session.query(Package)
            for pkg in packages:
                self.process_new_package(session, pkg)
        finally:
            session.close()

    def process_build_requires(self, session, pkg):
        invocation = get_repoquery_invocation()
        invocation += ['--requires', '--archlist', 'src', pkg.name]
        deps = subprocess.check_output(invocation).split('\n')
        deps = filter(None, deps)
        build_requires = []
        for dep in deps:
            invocation = get_repoquery_invocation()
            invocation += ['--file', dep]
            build_requires += subprocess.check_output(invocation).split('\n')
        build_requires = filter(None, build_requires)
        self.process_deps(session, pkg, build_requires, runtime=False)

    def process_requires(self, session, pkg):
        invocation = get_repoquery_invocation()
        invocation += ['--requires', '--resolve', 'src', pkg.name]
        deps = subprocess.check_output(invocation).split('\n')
        deps = filter(None, deps)
        self.process_deps(session, pkg, deps, runtime=True)

    def process_deps(self, session, pkg, deps, runtime):
        existing = {assoc.dependency.name: assoc for assoc in pkg.dependencies
                    if assoc.runtime == runtime}
        keep = set()
        for dep_name in set(deps):
            if dep_name in existing:
                keep.add(existing[dep_name])
            else:
                dep_pkg = session.query(Package).filter_by(name=dep_name).first()
                if not dep_pkg:
                    dep_pkg = Package(name=dep_name, watched=False)
                    session.add(dep_pkg)
                    session.commit()
                    self.process_new_package(session, dep_pkg)
                assoc = Dependency(package_id=pkg.id, dependency_id=dep_pkg.id,
                                   runtime=runtime)
                session.add(assoc)
                session.commit()
        for dep in set(existing.values()).difference(keep):
            session.delete(dep)
            session.commit()
