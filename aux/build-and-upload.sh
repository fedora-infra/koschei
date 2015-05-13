#!/bin/bash
set -e

cd "$(git rev-parse --show-toplevel)"
MOCK_CONFIG=epel-7-x86_64
VERSION="$(python setup.py -V)"
RELNO="$(git rev-list "$(git describe --tags --abbrev=0)..HEAD" --count)"
USERNAME="$(sed 's/@.*//' <<< "$EMAIL")"
test -n "$USERNAME"
git archive HEAD --prefix="koschei-$VERSION/"| gzip >koschei-${VERSION}.tar.gz
mkdir -p build
cd build
sed "s/^Release:[^%]*/&.$RELNO/" ../koschei.spec > koschei.spec
rpmbuild -bs -D"_sourcedir $PWD/.." -D"_srcrpmdir $PWD" koschei.spec
mock -r $MOCK_CONFIG -n --rebuild koschei-${VERSION}-*.$RELNO.*.src.rpm --resultdir .
createrepo_c .
RPM=`echo koschei-${VERSION}-*.$RELNO.*.noarch.rpm`
scp -r repodata "$RPM" "$USERNAME@fedorapeople.org:public_html/koschei/repo/"
echo "https://$USERNAME.fedorapeople.org/koschei/repo/$RPM"
