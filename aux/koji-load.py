#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (C) 2015-2016 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function
from six.moves import range as xrange

import koji
import sys
from concurrent.futures import ThreadPoolExecutor

arches = ['x86_64', 'armhfp', 'aarch64', 'ppc64', 'ppc64le', 's390x']
koji_urls = {'Production': 'https://koji.fedoraproject.org/kojihub',
             'Staging': 'https://koji.stg.fedoraproject.org/kojihub'}

def pretty_table(caption, tab, footer):
    widths = [max([len(str(row[col])) for row in tab]) for col in xrange(0, len(tab[0]))]
    def free_text(text):
        return text + " " * (sum([width + 3 for width in widths]) - len(text) + 1)
    def separator_row(begin, middle, end):
        return begin + middle.join(["─" * (width + 2) for width in widths]) + end
    def table_row(data):
        return "│ " + " │ ".join(" " * (width - len(str(item))) + str(item) for item, width in zip(data, widths)) + " │"
    return [free_text(caption), separator_row("┌", "┬", "┐"), table_row(tab[0]), separator_row("├", "┼", "┤")] + \
            [table_row(row) for row in tab[1:]] + [separator_row("└", "┴", "┘"), free_text(footer)]

def gather_data(env):
    session = koji.ClientSession(koji_urls[env])
    channel = session.getChannel('default')
    hosts = session.listHosts(arches, channel['id'], enabled=True)
    rows = [['arch', 'builders', 'capacity', 'load', 'rel load']]
    max_load = 0
    for arch in arches:
        arch_hosts = [host for host in hosts if arch in host['arches']]
        capacity = sum(host['capacity'] for host in arch_hosts)
        load = sum(min(host['task_load'], host['capacity']) if host['ready']
                   else host['capacity'] for host in arch_hosts)
        relative_load = "%3.2f %%" % (100 * load / capacity) if capacity else "N/A"
        max_load = max(max_load, 100 * load / capacity) if capacity else max_load
        rows.append([arch, len(arch_hosts), "%3.2f" % capacity, "%3.2f" % load, relative_load])
    return pretty_table(env + ":", rows, "Effective load: %3.2f %%" % max_load)

with ThreadPoolExecutor(max_workers=16) as executor:
    result = [f.result() for f in [executor.submit(gather_data, env) for env in sorted(koji_urls.keys())]]
    for a, b in zip(*result):
        print("   ".join([a, b]))
