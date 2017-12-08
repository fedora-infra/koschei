# Copyright (C) 2014-2017  Red Hat, Inc.
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

import humanize
from jinja2 import Markup

from koschei.frontend.base import app

app.add_template_filter(humanize.intcomma, 'intcomma')
app.add_template_filter(humanize.naturaltime, 'naturaltime')
app.add_template_filter(humanize.naturaldelta, 'naturaldelta')


@app.template_filter()
def percentage(val):
    return format(val * 10000, '.4f') + Markup('&nbsp;&#8241;')


@app.template_filter('date')
def date_filter(date):
    return date.strftime("%F %T") if date else ''


@app.template_filter()
def epoch(dt):
    return int((dt - datetime.fromtimestamp(0)).total_seconds())
