#!/bin/bash
set -e

cd "$(git rev-parse --show-toplevel)"
MOCK_CONFIG=fedora-20-x86_64
VERSION="$(python setup.py -V)"
RELNO="$(git rev-list "$(git describe --tags --abbrev=0)..HEAD" --count)"
USERNAME="$(sed 's/@.*//' <<< "$EMAIL")"
test -n "$USERNAME"
git archive HEAD --prefix="koschei-koschei-$VERSION/"| gzip >koschei-${VERSION}.tar.gz
mkdir -p build
cd build
sed "s/^Release:[^%]*/&.$RELNO/" ../koschei.spec > koschei.spec
mock -r $MOCK_CONFIG -n --buildsrpm --spec koschei.spec --sources .. --resultdir .
mock -r $MOCK_CONFIG -n --rebuild koschei-${VERSION}-*.$RELNO.*.src.rpm --resultdir .
createrepo_c .
scp -r repodata koschei-${VERSION}-*.$RELNO.*.noarch.rpm "$USERNAME@fedorapeople.org:public_html/koschei/repo/"
