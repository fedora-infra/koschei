#!/bin/bash
set -e
drop() {
    dropdb koschei_it --if-exists &>/dev/null
}
trap drop EXIT
getdata() {
    if [ ! -f "$1" ]; then
        wget "https://msimacek.fedorapeople.org/$1"
    fi
}
repo_id=509557
itdir=`dirname "$0"`
cd "$itdir"
getdata koschei-it-repo.tar.bz2
getdata koschei-it-dump.sql.xz
if [ ! -d repodata ]; then
    mkdir repodata
    tar xf koschei-it-repo.tar.bz2 -C repodata
fi
drop
createdb koschei_it
xzcat koschei-it-dump.sql.xz | sed 's/DATABASE koschei/&_it/;s/\\connect koschei/&_it/;' | psql koschei_it
psql koschei_it <<< "UPDATE repo SET base_resolved = TRUE;"
cd ..
export KOSCHEI_CONFIG='config.cfg.template:it/config.cfg'
alembic upgrade head
time python -c "
import mock, json
from koschei import resolver, util
task = resolver.Resolver(koji_session=mock.Mock()).create_task(resolver.GenerateRepoTask)
brs = json.load(open('it/get_rpm_requires.json'))
util.get_rpm_requires = lambda _, ps: [brs[p['name']] for p in ps]
group = json.load(open('it/get_build_group.json'))
with mock.patch.object(task.backend, 'refresh_latest_builds'):
    with mock.patch('koschei.util.get_build_group', return_value=group):
        task.run($repo_id)
"
psql koschei_it > it/actual.out <<EOF
SELECT id, name, resolved
  FROM package
  ORDER BY id;
SELECT package_id, dep_name, prev_epoch, prev_version, prev_release,
    curr_epoch, curr_version, curr_release, distance
  FROM dependency_change
  WHERE applied_in_id IS NULL
  ORDER BY package_id, dep_name;
SELECT package_id, problem
  FROM resolution_problem
  ORDER BY package_id, problem;
EOF
diff it/{expected,actual}.out > it/differences.out || {
    echo "Test failed, check it/differences.out"
    exit 1
}
