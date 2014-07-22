#!/usr/bin/python
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

import os

from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import joinedload

from . import util, scheduler
from .models import Package, PackageGroup
from .service import service_main

jinja_env = Environment(loader=FileSystemLoader(util.config['directories']['templates']))

log_output_dir = util.config['directories']['build_logs']
relative_logdir = util.config['directories']['build_logs_relative']
outdir = util.config['directories']['reports']
base_vars = {'log_dir': relative_logdir,
             'koji_weburl': util.config['koji_config']['weburl']}

def date_filter(date):
    if date:
        return date.strftime("%F %T")

jinja_env.filters['date'] = date_filter

def generate_page(template_name, filename=None, **kwargs):
    filename = filename or template_name
    template = jinja_env.get_template(template_name)
    context = dict(base_vars)
    context['toplink'] = os.path.relpath('.', os.path.dirname(filename))
    context.update(kwargs)
    content = template.render(**context)
    path = os.path.join(outdir, filename)
    with open(path, 'w') as f:
        f.write(content)

def generate_frontpage(session, since, until):
    packages = session.query(Package)\
                      .options(joinedload(Package.last_build))\
                      .order_by(Package.id).all()
    generate_page('frontpage.html', 'index.html', packages=packages,
                  since=since, until=until)

def generate_groups(session):
    util.mkdir_if_absent(os.path.join(outdir, 'group'))
    groups = session.query(PackageGroup).order_by(PackageGroup.name).all()
    generate_page('groups.html', groups=groups)
    for group in groups:
        path = os.path.join('group', str(group.id)) + '.html'
        generate_page('group-detail.html', path, group=group)

def generate_details(session):
    packages = session.query(Package)\
                      .options(joinedload(Package.all_builds)).all()
    priorities = scheduler.get_priority_queries(session)
    priorities = [(name, dict(priority)) for name, priority in priorities.items()]

    for package in packages:
        path = os.path.join('package', package.name) + '.html'
        generate_page('package-detail.html', path, package=package)
        for build in package.all_builds:
            pkg_dir = os.path.join('package', package.name)
            util.mkdir_if_absent(os.path.join(outdir, pkg_dir))
            build_path = os.path.join(pkg_dir, str(build.id)) + '.html'
            generate_page('build-detail.html', build_path, build=build)

@service_main(needs_koji=False)
def main(db_session):
    util.mkdir_if_absent(os.path.join(outdir, 'package'))
    since = datetime.min
    until = datetime.now()
    generate_frontpage(db_session, since, until)
    generate_groups(db_session)
    generate_details(db_session)
