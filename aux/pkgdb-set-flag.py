#!/usr/bin/python

# Set Koschei monitoring flag for <PACKAGES> to <VALUE> in pkgdb2 instance at
# <PKGDB_URL>.  Login credentials are provided in <FAS_CONF> file.
# Requires: packagedb-cli >= 2.9.
PACKAGES = ['pkg1', 'pkg2']
VALUE = True
PKGDB_URL = 'https://admin.stg.fedoraproject.org/pkgdb'
FAS_CONF = '/etc/fas.conf'


from pkgdb2client import PkgDB
from ConfigParser import ConfigParser

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
    print "%s: %s" % (package, message)

print "Done."
