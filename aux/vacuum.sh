#!/bin/sh
set -e

MACHINE=koschei.cloud.fedoraproject.org

ssh koschei@$MACHINE psql <<"EOF"
delete from dependency where repo_id != (select max(id) from repo);
vacuum;
EOF
