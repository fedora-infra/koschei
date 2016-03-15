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
pip install alembic==0.7.4
pip install dogpile.cache==0.5.5
pip install --upgrade nose
pip install --upgrade nosexcover
pip install --upgrade coverage
pip install --upgrade pylint
pip install --upgrade pep8

hash -r

TEST_WITH_FAITOUT=1 nosetests --with-xunit --cover-erase --cover-branches --cover-package=koschei --with-xcoverage

checked_files=`find koschei/ admin.py -name '*.py'`
pylint --msg-template="{path}:{line}: [{msg_id}({symbol}), {obj}] {msg}" \
    --rcfile aux/pylintrc $checked_files | tee pylint.out
pep8 --config=aux/pep8.cfg $checked_files | tee pep8.out
