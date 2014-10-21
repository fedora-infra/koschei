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

import koji
import librepo
import logging
import os
import shutil
import subprocess
import rpm

from koschei import util

log = logging.getLogger('koschei.srpm_cache')

pathinfo = koji.PathInfo(topdir=util.koji_config['topurl'])
source_tag = util.koji_config['source_tag']


class SRPMCache(object):

    def __init__(self, koji_session,
                 srpm_dir=util.config['directories']['srpms']):
        self._srpm_dir = srpm_dir
        self._koji_session = koji_session
        repodata_dir = os.path.join(srpm_dir, 'repodata')
        if os.path.exists(repodata_dir):
            shutil.rmtree(repodata_dir)
        self._cache = {}
        self._dirty = True
        self._read_existing_srpms()

    def _read_existing_srpms(self):
        srpms = os.listdir(self._srpm_dir)
        ts = rpm.TransactionSet()
        for srpm in srpms:
            if not srpm.endswith('.rpm'):
                continue
            path = os.path.join(self._srpm_dir, srpm)
            try:
                fd = os.open(path, os.O_RDONLY)
                hdr = ts.hdrFromFdno(fd)
                nevr = (hdr['name'], hdr['epoch'], hdr['version'],
                        hdr['release'])
                self._cache[nevr] = path
            except rpm.error as e:
                log.warn("Unreadable rpm in srpm_dir: {}\nRPM error: {}"
                         .format(path, e.message))
            finally:
                if fd:
                    os.close(fd)

    def get_srpm(self, name, epoch, version, release):
        nevr = name, epoch, version, release
        cached = self._cache.get(nevr)
        if cached:
            return cached
        self._dirty = True
        builds = self._koji_session.listTagged(source_tag, package=name)
        for build in builds:
            if (build['epoch'] == epoch and build['version'] == version and
                    build['release'] == release):
                srpms = self._koji_session.listRPMs(buildID=build['build_id'],
                                                    arches='src')
                if srpms:
                    build_url = pathinfo.build(build)
                    srpm_name = pathinfo.rpm(srpms[0])
                    path = util.download_rpm_header(
                        build_url + '/' + srpm_name, self._srpm_dir)
                    self._cache[nevr] = path
                    return path

    def get_latest_srpms(self, package_names):
        while package_names:
            self._koji_session.multicall = True
            for package_name in package_names[:50]:
                self._koji_session.listTagged(source_tag, latest=True,
                                              package=package_name)
            urls = []
            infos = self._koji_session.multiCall()
            self._koji_session.multicall = True
            for [info] in infos:
                if info:
                    self._koji_session.listRPMs(buildID=info[0]['build_id'],
                                                arches='src')
                    urls.append(pathinfo.build(info[0]))
            srpms = self._koji_session.multiCall()
            assert len(srpms) == len(urls)
            for [srpm], build_url in zip(srpms, urls):
                srpm = srpm[0]
                srpm_url = pathinfo.rpm(srpm)
                srpm_name = os.path.basename(srpm_url)
                util.download_rpm_header(
                    build_url + '/' + srpm_url, self._srpm_dir)
                nevr = (srpm['name'], srpm['epoch'], srpm['version'],
                        srpm['release'])
                self._cache[nevr] = os.path.join(self._srpm_dir, srpm_name)
                self._dirty = True
            package_names = package_names[50:]

    def _createrepo(self):
        log.debug('createrepo_c')
        createrepo = subprocess.Popen(['createrepo_c', self._srpm_dir],
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        out, err = createrepo.communicate()
        ret = createrepo.wait()
        if ret:
            raise Exception("Createrepo failed: return code {ret}\n{err}"
                            .format(ret=ret, err=err))
        log.debug(out)
        self._dirty = False

    def get_repodata(self):
        if self._dirty:
            self._createrepo()
        h = librepo.Handle()
        h.local = True
        h.repotype = librepo.LR_YUMREPO
        h.urls = [self._srpm_dir]
        return h.perform(librepo.Result())
