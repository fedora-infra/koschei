#!/bin/bash

virtualenv koscheienv --system-site-packages
source koscheienv/bin/activate

pip install sqlalchemy==0.9.7
pip install fedmsg==0.14
pip install mock==1.0.1
pip install --upgrade nose
pip install --upgrade nosexcover
pip install --upgrade coverage

TEST_WITH_FAITOUT=1 koscheienv/bin/nosetests --with-xunit --cover-erase --cover-package=koschei --with-xcoverage

pylint -f parseable --rcfile aux/pylintrc `find koschei/ admin.py -name '*.py'`
pep8 koschei/*.py koschei/*/*.py | tee pep8.out
