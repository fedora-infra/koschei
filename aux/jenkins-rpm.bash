#!/bin/bash
set -e
VERSION="$(python setup.py -V)"
git archive HEAD --prefix="koschei-$VERSION/"| gzip >"koschei-${VERSION}".tar.gz
mkdir -p build rpms
cd build
rm -f *.src.rpm
sed "s/^Release:[^%]*/&.jenkins$BUILD_NUMBER/" ../koschei.spec > koschei.spec
rpmbuild -bs -D"_sourcedir $PWD/.." -D"_srcrpmdir $PWD" koschei.spec
mock -r epel-7-x86_64 --rebuild koschei-*.src.rpm --resultdir ../rpms

cd ../rpms
# Keep last 5 RPMS (and 5 SRPMS)
python -c 'import os,glob;map(os.unlink, sorted(glob.glob("*.rpm"), key=os.path.getmtime)[0:-10])'
createrepo_c .
