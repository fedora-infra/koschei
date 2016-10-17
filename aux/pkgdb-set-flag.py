#!/usr/bin/python
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

# Set Koschei monitoring flag for <PACKAGES> to <VALUE> in pkgdb2 instance at
# <PKGDB_URL>.  Login credentials are provided in <FAS_CONF> file.
# Requires: packagedb-cli >= 2.9.
PACKAGES = ['pkg1', 'pkg2']
VALUE = True
PKGDB_URL = 'https://admin.stg.fedoraproject.org/pkgdb'
FAS_CONF = '/etc/fas.conf'


from pkgdb2client import PkgDB
from six.moves.configparser import ConfigParser

# Obtain FAS credentials
conf = ConfigParser()
conf.read(FAS_CONF)
login = conf.get('global', 'login')
password = conf.get('global', 'password')

# Initiate authenticated pkgdb2 session
pkgdb = PkgDB(PKGDB_URL)
pkgdb.login(login, password)

# Set package monitoring status one-by-one
for package in PACKAGES:
    result = pkgdb.set_koschei_status(package, VALUE)
    message = result.get('messages', 'Invalid output')
    print("%s: %s" % (package, message))

print("Done.")
