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

from plugins import Plugin, _Meta
from models import Build, BuildTrigger

class TimePlugin(Plugin):
    order = 9999

    def __init__(self):
        super(TimePlugin, self).__init__()
        self.register_event('get_priority_query', self.get_priority_query)
        self.register_event('build_submitted', self.populate_triggers)

    def get_priority_query(self, db_session):
        q = db_session.query(Build.package_id, Build.time_since_last_build_expr())\
                      .group_by(Build.package_id)
        return q.subquery()

    def populate_triggers(self, db_session, build):
        if not db_session.query(build.triggered_by.exists()).scalar():
            since = Build.time_since_last_build_expr()
            hours = db_session.query(since)\
                              .filter(Build.id != build.id)\
                              .filter(Build.package_id == build.package_id)\
                              .group_by(Build.package_id)\
                              .order_by(since)\
                              .first()
            if hours:
                comment = "Package hasn't been rebuilt for {} hours"\
                          .format(int(hours[0]))
                trigger = BuildTrigger(build_id=build.id, comment=comment)
                db_session.add(trigger)
                db_session.commit()
