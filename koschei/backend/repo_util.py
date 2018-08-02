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

"""
Utility functions for downloading repos.
"""

import os
import hawkey
import librepo
import shutil

from koschei.config import get_config


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
        if e.args[0] == librepo.LRE_NOURL:
            return None
        raise
    repodata = result.yum_repo
    repo = hawkey.Repo(str(repo_descriptor))
    repo.repomd_fn = repodata['repomd']
    repo.primary_fn = repodata['primary']
    repo.filelists_fn = repodata['filelists']
    return repo


def get_comps_path(repo_dir, repo_descriptor):
    """
    Returns path to comps for given repo in repo_dir. It needs to be downloaded
    already.

    :repo_dir: path to directory where the repo is stored
    :repo_descriptor: which repo to obtain
    """
    h = librepo.Handle()
    repo_path = os.path.join(repo_dir, str(repo_descriptor))
    h.repotype = librepo.LR_YUMREPO
    h.urls = [repo_path]
    h.local = True
    h.yumdlist = ['group']
    result = h.perform(librepo.Result())
    return result.yum_repo.get('group')


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
        sack.load_repo(repo, load_filelists=True, build_cache=download)
        return sack
