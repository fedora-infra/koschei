# Copyright (C) 2016  Red Hat, Inc.
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
import copr.v3

from koschei.config import get_config
from koschei.models import CoprRebuildRequest
from koschei.backend.koji_util import KojiRepoDescriptor

copr_client = copr.v3.Client.create_from_config_file(
    get_config('copr.config_path')
)


class RequestProcessingError(Exception):
    pass


def get_request_cachedir(request_or_id):
    "Returns path to request cache directory"
    if isinstance(request_or_id, CoprRebuildRequest):
        request_id = request_or_id.id
    else:
        request_id = request_or_id
    return os.path.join(
        get_config('directories.cachedir'),
        'user_repos',
        'copr-request-{}'.format(request_id),
    )


def get_request_comps_path(request):
    "Returns path to comps.xml file for given request"
    return os.path.join(get_request_cachedir(request), 'comps.xml')


def repo_descriptor_for_request(request):
    collection = request.collection
    return KojiRepoDescriptor(
        koji_id='secondary' if collection.secondary_mode else 'primary',
        repo_id=request.repo_id,
        build_tag=collection.build_tag,
    )


def prepare_comps(session, request, descriptor):
    os.makedirs(get_request_cachedir(request), exist_ok=True)
    comps = session.repo_cache.get_comps_path(descriptor)
    target = get_request_comps_path(request)
    # hardlink
    os.link(comps, target)
