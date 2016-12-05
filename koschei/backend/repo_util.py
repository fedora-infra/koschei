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
import hawkey
import librepo
import shutil

from functools import total_ordering

from koschei.config import get_config, get_koji_config


REPO_404 = 19


@total_ordering
class KojiRepoDescriptor(object):
    def __init__(self, koji_id, build_tag, repo_id):
        self.koji_id = koji_id
        self.build_tag = build_tag
        self.repo_id = repo_id

    @staticmethod
    def from_string(name):
        parts = name.split('-')
        if len(parts) < 3 or not parts[-1].isdigit():
            return None
        return KojiRepoDescriptor(parts[0], '-'.join(parts[1:-1]), int(parts[-1]))

    def __str__(self):
        return '{}-{}-{}'.format(self.koji_id, self.build_tag, self.repo_id)

    def __hash__(self):
        return hash((self.koji_id, self.build_tag, self.repo_id))

    def __eq__(self, other):
        try:
            return (self.koji_id == other.koji_id and
                    self.build_tag == other.build_tag and
                    self.repo_id == other.repo_id)
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.repo_id < other.repo_id

    @property
    def url(self):
        arch = get_config('dependency.repo_arch')
        topurl = get_koji_config(self.koji_id, 'topurl')
        url = '{topurl}/repos/{build_tag}/{repo_id}/{arch}'
        return url.format(topurl=topurl, build_tag=self.build_tag,
                          repo_id=self.repo_id, arch=arch)


def get_repo(repo_dir, repo_descriptor, download=False):
    """
    Obtain hawkey Repo either by loading from disk, or downloading from
    Koji.

    :repo_dir: path to directory where the repo is/should be stored
    :repo_descriptor: which repo to obtain
    :download: whether to download or load locally
    """
    h = librepo.Handle()
    repo_path = os.path.join(repo_dir, str(repo_descriptor))
    if download:
        h.destdir = repo_path
        shutil.rmtree(repo_path, ignore_errors=True)
        os.makedirs(os.path.join(repo_path, 'cache'))
    h.repotype = librepo.LR_YUMREPO
    h.urls = [repo_descriptor.url if download else repo_path]
    h.local = not download
    h.yumdlist = ['primary', 'filelists', 'group', 'group_gz']
    result = librepo.Result()
    try:
        result = h.perform(result)
    except librepo.LibrepoException as e:
        if e.args[0] == REPO_404:
            return None
        raise
    repodata = result.yum_repo
    repo = hawkey.Repo(str(repo_descriptor))
    repo.repomd_fn = repodata['repomd']
    repo.primary_fn = repodata['primary']
    repo.filelists_fn = repodata['filelists']
    return repo


def load_sack(repo_dir, repo_descriptor, download=False):
    """
    Obtain hawkey Sack either by loading from disk, or downloading from
    Koji. Builds cache when downloading.

    :repo_dir: path to directory where the repo is/should be stored
    :repo_descriptor: which repo to obtain
    :download: whether to download or load locally
    """
    cache_dir = os.path.join(repo_dir, str(repo_descriptor), 'cache')
    for_arch = get_config('dependency.resolve_for_arch')
    sack = hawkey.Sack(arch=for_arch, cachedir=cache_dir)
    repo = get_repo(repo_dir, repo_descriptor, download)
    if repo:
        sack.load_yum_repo(repo, load_filelists=True, build_cache=download)
        return sack
