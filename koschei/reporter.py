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
import sys
import time

from datetime import datetime
from collections import defaultdict
from jinja2 import Environment, FileSystemLoader

from . import models, util, plugin

jinja_env = Environment(loader=FileSystemLoader(util.config['directories']['report_templates']))

log_output_dir = util.config['directories']['build_logs']
relative_logdir = util.config['directories']['build_logs_relative']

def date_filter(date):
    return date.strftime("%x %X")

jinja_env.filters['date'] = date_filter

def generate_report(session, template, since, until):
    template = jinja_env.get_template(template)
    packages = session.query(models.Package)\
               .order_by(models.Package.id).all()
    priorities = plugin.dispatch_event('get_priority_query', session, return_name=True)
    priorities = [(name, dict(priority)) for name, priority in priorities]
    # FIXME remember this in DB
    builds = session.query(models.Build.id)
    root_diffs = defaultdict(dict)
    for [build_id] in builds:
        logdir = os.path.join(log_output_dir, str(build_id))
        if not os.path.isdir(logdir):
            continue
        arches = os.listdir(logdir)
        for arch in arches:
            diff_path = os.path.join(logdir, arch, 'root_diff.log')
            if os.path.exists(diff_path):
                root_diffs[build_id][arch] = os.path.join(relative_logdir, str(build_id), arch, 'root_diff.log')
    return template.render(packages=packages, since=since, until=until, models=models,
                           root_diffs=root_diffs, log_dir=relative_logdir,
                           priorities=priorities,
                           koji_weburl=util.config['koji_config']['weburl'])

def main():
    session = models.Session()
    if len(sys.argv) > 1:
        template_name = sys.argv[1]
    else:
        template_name = util.config['reports']['default_template']
    while True:
        since = datetime.min
        until = datetime.now()
        report_path = os.path.join(util.config['directories']['reports'], 'index.html')
        report = generate_report(session, template_name, since, until)
        with open(report_path, 'w') as report_file:
            report_file.write(report)
        time.sleep(1)

if __name__ == '__main__':
    main()
