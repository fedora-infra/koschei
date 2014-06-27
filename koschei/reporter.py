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
from jinja2 import Environment, FileSystemLoader

from . import models, util

jinja_env = Environment(loader=FileSystemLoader(util.config['directories']['report_templates']))

def date_filter(date):
    return date.strftime("%x %X")

jinja_env.filters['date'] = date_filter

def installed_pkgs_from_log(root_log):
    with open(root_log) as log:
        pkgs = []
        lines = log.read().split('\n')
        reading = False
        start_delimiters = ('Installed:', 'Dependency Installed:')
        for line in lines:
            if any(line.rstrip().endswith(section) for section
                   in start_delimiters):
                reading = True
            elif 'Child return code was:' in line:
                reading = False
            elif reading:
                pkg_line = line.split()[2:]
                pkgs += [p1 + ' ' + p2 for p1, p2 in zip(pkg_line[::2], pkg_line[1::2])]
        return pkgs

def log_diff(session, build1, build2):
    if not build1.logs_downloaded or not build2.logs_downloaded:
        return {}
    log_output_dir = util.config['directories']['build_logs']
    logdir1 = os.path.join(log_output_dir, str(build1.id))
    logdir2 = os.path.join(log_output_dir, str(build2.id))
    logdiffs = {}
    for arch in os.listdir(logdir1):
        pkgs1 = set(installed_pkgs_from_log(os.path.join(logdir1, arch, 'root.log')))
        pkgs2 = set(installed_pkgs_from_log(os.path.join(logdir2, arch, 'root.log')))
        if arch == 'noarch':
            pkgs1 = {'.'.join(pkg.split('.')[:-1]) for pkg in pkgs1}
            pkgs2 = {'.'.join(pkg.split('.')[:-1]) for pkg in pkgs2}
        diff = ['+ {}'.format(pkg) for pkg in pkgs1.difference(pkgs2)]
        diff += ['- {}'.format(pkg) for pkg in pkgs2.difference(pkgs1)]
        logdiffs[arch] = sorted(diff, key=lambda x: x[1:])
    return logdiffs

def generate_report(session, template, since, until):
    template = jinja_env.get_template(template)
    packages = session.query(models.Package)\
               .order_by(models.Package.id).all()
    return template.render(packages=packages, since=since, until=until, models=models,
                           log_diff=lambda b1, b2: log_diff(session, b1, b2),
                           log_dir=util.config['directories']['build_logs_relative'],
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
