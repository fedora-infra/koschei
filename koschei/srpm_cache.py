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

from koschei import util

log = logging.getLogger('srpm_cache')

pathinfo = koji.PathInfo(topdir=util.koji_config['topurl'])
source_tag = util.koji_config['source_tag']

class SRPMCache(object):

    def __init__(self, koji_session, srpm_dir=util.config['directories']['srpms']):
        self._srpm_dir = srpm_dir
        self._koji_session = koji_session
        srpms = os.listdir(srpm_dir)
        self._cache = {}
        for srpm in srpms:
            path = os.path.join(srpm_dir, srpm)
            try:
                out = subprocess.check_output(['rpm', '-qp', path,
                                               '--qf=%{name}#%{epoch}#%{version}#%{release}'])
                nevr = out.split('#')
                nevr[1] = int(nevr[1]) if nevr[1] != '(none)' else None
                self._cache[tuple(nevr)] = path
            except subprocess.CalledProcessError:
                pass

    def get_srpm(self, name, epoch, version, release):
        nevr = name, epoch, version, release
        cached = self._cache.get(nevr)
        if cached:
            return cached
        builds = self._koji_session.listTagged(source_tag, package=name)
        for build in builds:
            if (build['epoch'] == epoch and build['version'] == version and
                    build['release'] == release):
                srpms = self._koji_session.listRPMs(buildID=build['build_id'], arches='src')
                if srpms:
                    build_url = pathinfo.build(build)
                    srpm_name = pathinfo.rpm(srpms[0])
                    path = util.download_rpm_header(build_url + '/' + srpm_name, self._srpm_dir)
                    self._cache[nevr] = path
                    return path

    def get_latest_srpms(self, package_names):
        while package_names:
            self._koji_session.multicall = True
            for package_name in package_names[:50]:
                self._koji_session.listTagged(source_tag, latest=True, package=package_name)
            urls = []
            infos = self._koji_session.multiCall()
            self._koji_session.multicall = True
            for [info] in infos:
                if info:
                    self._koji_session.listRPMs(buildID=info[0]['build_id'], arches='src')
                    urls.append(pathinfo.build(info[0]))
            srpms = self._koji_session.multiCall()
            for [srpm], url in zip(srpms, urls):
                srpm_name = pathinfo.rpm(srpm[0])
                util.download_rpm_header(url + '/' + srpm_name, self._srpm_dir)
            package_names = package_names[50:]

    def createrepo(self):
        log.debug('createrepo_c')
        createrepo = subprocess.Popen(['createrepo_c', self._srpm_dir], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        out, err = createrepo.communicate()
        ret = createrepo.wait()
        if ret:
            raise Exception("Createrepo failed: return code {ret}\n{err}"
                            .format(ret=ret, err=err))
        log.debug(out)

    def get_repodata(self):
        h = librepo.Handle()
        h.local = True
        h.repotype = librepo.LR_YUMREPO
        h.urls = [self._srpm_dir]
        return h.perform(librepo.Result())
