#!/bin/bash

virtualenv koscheienv --system-site-packages
source koscheienv/bin/activate

pip install sqlalchemy==0.9.8
pip install fedmsg==0.15.0
pip install mock==1.0.1
pip install wtforms==2.0
pip install jinja2==2.7.2
pip install flask==0.10.1
pip install flask-sqlalchemy==2.0
pip install flask-wtf==0.8
pip install flask-openid==1.2.3
pip install --upgrade nose
pip install --upgrade nosexcover
pip install --upgrade coverage
pip install --upgrade pylint
pip install --upgrade pep8

hash -r

TEST_WITH_FAITOUT=1 nosetests --with-xunit --cover-erase --cover-package=koschei --with-xcoverage

pylint -f parseable --rcfile aux/pylintrc `find koschei/ admin.py -name '*.py'`
pep8 koschei/*.py koschei/*/*.py | tee pep8.out

VERSION="$(python setup.py -V)"
git archive HEAD --prefix="koschei-$VERSION/"| gzip >"koschei-${VERSION}".tar.gz
mkdir -p build rpms
cd build
rm -f *.src.rpm
sed "s/^Release:[^%]*/&.jenkins$BUILD_NUMBER/" ../koschei.spec > koschei.spec
rpmbuild -bs -D"_sourcedir $PWD/.." -D"_srcrpmdir $PWD" koschei.spec
mock -r epel-7-x86_64 --rebuild koschei-*.src.rpm --resultdir ../rpms
createrepo_c ../rpms
