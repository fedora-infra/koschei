#!/bin/bash
. aux/set-env.sh
pg_init
pg_start
nosetests-3 --with-coverage --cover-xml

test -n "$TRAVIS" && bash <(curl -s https://codecov.io/bash)
