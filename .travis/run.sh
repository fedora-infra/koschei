#!/bin/bash
. aux/set-env.sh
pg_init
pg_start
nosetests-3
