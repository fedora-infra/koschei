#!/bin/bash
set -e
cd "$(git rev-parse --show-toplevel)"
MOCK_CONFIG="${MOCK_CONFIG:-epel-7-x86_64}"
VERSION="$(python setup.py -V)"
RELNO="$(git rev-list "$(git describe --tags --abbrev=0)..HEAD" --count)"
USERNAME="$(sed 's/@.*//' <<< "$EMAIL")"
git archive HEAD --prefix="koschei-$VERSION/"| gzip >koschei-${VERSION}.tar.gz
rm -r build
mkdir build
cd ./build
sed "s/^Release:[^%]*/&.$RELNO/" ../koschei.spec > koschei.spec
rpmbuild -bs -D"_sourcedir $PWD/.." -D"_srcrpmdir $PWD" koschei.spec
mock -r "$MOCK_CONFIG" -n koschei-${VERSION}-*.$RELNO.*.src.rpm --resultdir .
