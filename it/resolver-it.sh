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
DATA_REV=2
DUMP=koschei-it-dump-$DATA_REV.sql.xz
REPO=koschei-it-repo-$DATA_REV.tar.bz2
getdata $REPO
getdata $DUMP
rm -r repodata
mkdir repodata
tar xf $REPO -C repodata
drop
createdb koschei_it
xzcat $DUMP | sed 's/DATABASE koschei/&_it/;s/\\connect koschei/&_it/;' | psql koschei_it
cd ..
export KOSCHEI_CONFIG='config.cfg.template:it/config.cfg'
alembic upgrade head
time python -c "
import mock, json
from koschei import models, config
from koschei.backend import koji_util
from koschei.backend.services import resolver
from test.common import KojiMock
koji_mock = KojiMock()
koji_mock.repoInfo.return_value = {
    'create_event': 11870447,
    'create_ts': 1438936330.7918,
    'creation_time': '2015-08-07 08:32:10.791796',
    'id': 509557,
    'state': 1,
    'tag_id': 315,
    'tag_name': 'f25-build'}
brs = json.load(open('it/get_rpm_requires.json'))
koji_util.get_rpm_requires = lambda _, ps: [brs[p['name']] for p in ps]
group = json.load(open('it/get_build_group.json'))
config.load_config([])
with mock.patch('koschei.backend.koji_util.get_build_group', return_value=group):
    with mock.patch('fedmsg.publish'):
        db = models.Session()
        collection = db.query(models.Collection).get(1)
        res = resolver.Resolver(koji_sessions={'primary': koji_mock, 'secondary': koji_mock},
                                db=db)
        res.generate_repo(collection, $repo_id)
"
psql koschei_it > it/actual.out <<EOF
SELECT id, name, resolved
  FROM package
  ORDER BY id;
SELECT package_id, dep_name, prev_epoch, prev_version, prev_release,
    curr_epoch, curr_version, curr_release, distance
  FROM unapplied_change
  ORDER BY package_id, dep_name;
SELECT package_id, problem
  FROM resolution_problem
  ORDER BY package_id, problem;
EOF
diff it/{expected,actual}.out > it/differences.out || {
    echo "Test failed, check it/differences.out"
    exit 1
}
