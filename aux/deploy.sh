#!/bin/sh
set -e

cd "$(git rev-parse --show-toplevel)"

MACHINE=koschei.cloud.fedoraproject.org
MOCK_CONFIG=fedora-20-x86_64
pushd systemd
SERVICES="httpd $(echo *.service)"
popd
VERSION="$(python setup.py -V)"

git archive HEAD --prefix="koschei-koschei-$VERSION/"| gzip >koschei-${VERSION}.tar.gz
DIST=`mock -r $MOCK_CONFIG -q --chroot 'rpm -E "%dist"'`
RELEASE="$(sed -n 's/^Release:\s*\([^%]*\).*/\1/p' koschei.spec)$DIST"
mkdir -p result
mock -r $MOCK_CONFIG -n --buildsrpm --spec koschei.spec --sources . --resultdir result/
mock -r $MOCK_CONFIG -n --rebuild result/koschei-${VERSION}-${RELEASE}.src.rpm --resultdir result/
cat result/koschei-$VERSION-${RELEASE}.noarch.rpm | ssh root@$MACHINE "

set -e
trap 'rm -f koschei.rpm' 0

cat >koschei.rpm
# stop for possible migration
for service in $SERVICES; do systemctl stop \$service; done
if [ \`rpm -q --qf='%{version}-%{release}' koschei\` == $VERSION-$RELEASE ]; then
yum reinstall -y koschei.rpm
else
yum upgrade -y koschei.rpm
fi

cd /usr/share/koschei
su koschei -c 'alembic upgrade head'

systemctl daemon-reload
for service in $SERVICES; do systemctl start \$service; done
sleep 1
for service in $SERVICES; do systemctl status \$service; done
"
