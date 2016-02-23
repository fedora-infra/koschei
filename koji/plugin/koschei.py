# Copyright (C) 2016 Red Hat, Inc.
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

from koji.context import context
from koji.plugin import export
import koji
import sys

sys.path.insert(0, '/usr/share/koji-hub/')
from kojihub import _singleValue, get_tag, InsertProcessor

__all__ = ('koscheiCreateRepo')

@export
def koscheiCreateRepo(repo_id, tag):
    """
    Add repo with specified repo_id for given tag and mark it as ready.
    Calling user must have repo or admin permission.  Specified
    repo_id must not exist yet.  Specified tag must be a valid tag
    name.
    """
    context.session.assertPerm('repo')

    event_id = _singleValue("SELECT get_event()")
    tag_id = get_tag(tag, strict=True)['id']
    state = koji.REPO_READY

    insert = InsertProcessor('repo')
    insert.set(id=repo_id, create_event=event_id, tag_id=tag_id, state=state)
    insert.execute()
