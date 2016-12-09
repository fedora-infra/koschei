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

from __future__ import print_function, absolute_import

import os
import copr

from koschei.config import get_config
from koschei.models import CoprRebuildRequest

copr_client = copr.client.CoprClient.create_from_file_config(
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
