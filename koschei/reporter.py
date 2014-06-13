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

from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from koschei import models

jinja_env = Environment(loader=FileSystemLoader('./report-templates'))

def date_filter(date):
    return date.strftime("%x %X")

jinja_env.filters['date'] = date_filter

def generate_report(template, since, until):
    session = models.Session()
    template = jinja_env.get_template(template)
    packages = session.query(models.Package).filter_by(watched=True)\
               .order_by(models.Package.id).all()
    return template.render(packages=packages, since=since, until=until,
                           models=models)

if __name__ == '__main__':
    since = datetime.min
    until = datetime.now()
    print generate_report('base-report.html', since, until)
