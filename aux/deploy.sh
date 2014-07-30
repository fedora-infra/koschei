#!/bin/sh
set -e

MACHINE=koschei.cloud.fedoraproject.org

git archive master | xz >koschei-0.0.1.tar.xz
rpmbuild -bb koschei.spec
cat noarch/koschei-0.0.1-1.fc20.noarch.rpm | ssh root@$MACHINE '

set -e
trap "rm -f koschei.rpm" 0

cat >koschei.rpm
yum reinstall -y koschei.rpm

systemctl daemon-reload
for service in scheduler submitter watcher reporter log-downloader polling; do
    systemctl restart koschei-$service
done

'
