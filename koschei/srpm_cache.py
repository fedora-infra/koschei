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
import subprocess
import rpm
import glob

from koschei import util
from koschei.util import itercall

log = logging.getLogger('koschei.srpm_cache')

pathinfo = koji.PathInfo(topdir=util.koji_config['topurl'])
source_tag = util.koji_config['source_tag']


class SRPMCache(object):

    def __init__(self, koji_session,
                 srpm_dir=util.config['directories']['srpms']):
        self._srpm_dir = srpm_dir
        self._lock_path = os.path.join(srpm_dir, '.lock')
        self._koji_session = koji_session

    def _get_srpm_path(self, name, version, release):
        srpm_name = '{n}-{v}-{r}.src.rpm'.format(n=name, v=version, r=release)
        return os.path.join(self._srpm_dir, srpm_name)

    def _read_local_srpm(self, path):
        with util.lock(self._lock_path):
            ts = rpm.TransactionSet()
            if not os.path.exists(path):
                return
            try:
                fd = os.open(path, os.O_RDONLY)
                ts.hdrFromFdno(fd)
                return path
            except rpm.error as e:
                log.debug("Unreadable rpm in srpm_dir: {}\nRPM error: {}"
                          .format(path, e.message))
            finally:
                if fd:
                    os.close(fd)

    def get_srpm(self, name, version, release):
        path = self._get_srpm_path(name, version, release)
        local = self._read_local_srpm(path)
        if local:
            return local
        nvr = "{}-{}-{}".format(name, version, release)
        build = self._koji_session.getBuild(nvr)
        if build:
            srpms = self._koji_session.listRPMs(build['id'], arches='src')
            if srpms:
                build_url = pathinfo.build(build)
                srpm_name = pathinfo.rpm(srpms[0])
                with util.lock(self._lock_path):
                    util.download_rpm_header(build_url + '/' + srpm_name, path)
        # verify it downloaded
        local = self._read_local_srpm(path)
        if local:
            return local

    def get_latest_srpms(self, task_infos):
        urls = map(pathinfo.build, task_infos)
        srpms = itercall(self._koji_session, task_infos,
                         lambda k, i: k.listRPMs(buildID=i['build_id'],
                                                 arches='src'))

        for [srpm], build_url in zip(srpms, urls):
            srpm_url = pathinfo.rpm(srpm)
            path = self._get_srpm_path(srpm['name'], srpm['version'],
                                       srpm['release'])
            local = self._read_local_srpm(path)
            if not local:
                with util.lock(self._lock_path):
                    util.download_rpm_header(build_url + '/' + srpm_url, path)

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

    def get_repodata(self):
        with util.lock(self._lock_path):
            mtime = 0
            repodata_dir = os.path.join(self._srpm_dir, 'repodata')
            if os.path.exists(repodata_dir):
                mtime = os.path.getmtime(repodata_dir)
            if any(os.path.getmtime(f) > mtime for f in
                   glob.glob(os.path.join(self._srpm_dir, '*.src.rpm'))):
                self._createrepo()
            h = librepo.Handle()
            h.local = True
            h.repotype = librepo.LR_YUMREPO
            h.urls = [self._srpm_dir]
            return h.perform(librepo.Result())
