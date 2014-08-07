#!/bin/sh
set -e

MACHINE=koschei.cloud.fedoraproject.org

ssh koschei@$MACHINE psql <<"EOF"
vacuum;
EOF
