#!/bin/sh
set -e

cd "$(git rev-parse --show-toplevel)"

MACHINE=koschei.cloud.fedoraproject.org
pushd systemd
SERVICES=$(echo *.service)
popd
VERSION="$(python setup.py -V)"
RELEASE="$(rpmspec -q koschei.spec --qf='%{release}')"

git archive HEAD | xz >koschei-${VERSION}.tar.xz
rpmbuild -bb koschei.spec -D"_sourcedir $PWD" -D"_rpmdir $PWD"
cat noarch/koschei-$VERSION-${RELEASE}.noarch.rpm | ssh root@$MACHINE "

set -e
trap 'rm -f koschei.rpm' 0

cat >koschei.rpm
# stop for possible migration
for service in $SERVICES; do systemctl stop \$service; done
yum reinstall -y koschei.rpm

cd /usr/share/koschei
su koschei -c 'alembic upgrade head'

systemctl daemon-reload
for service in $SERVICES; do systemctl start \$service; done
sleep 1
for service in $SERVICES; do systemctl status \$service; done
"
