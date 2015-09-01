#!/bin/bash

virtualenv koscheienv --system-site-packages
source koscheienv/bin/activate

pip install sqlalchemy==0.9.7
pip install fedmsg==0.14
pip install mock==1.0.1
pip install --upgrade nose

TEST_WITH_FAITOUT=1 koscheienv/bin/nosetests --with-xunit
