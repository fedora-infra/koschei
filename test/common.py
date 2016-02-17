import os
import unittest
import shutil
import json

from mock import Mock

from test import use_postgres, use_faitout, testdir, is_x86_64
from koschei import models as m

workdir = '.workdir'

def postgres_only(fn):
    return unittest.skipIf(not use_postgres and not use_faitout,
                           "Requires postgres")(fn)

def x86_64_only(fn):
    return unittest.skipIf(not is_x86_64, "Requires x86_64 host")(fn)

class AbstractTest(unittest.TestCase):

    def __init__(self, *args, **kwargs):
        super(AbstractTest, self).__init__(*args, **kwargs)
        self.oldpwd = os.getcwd()

    def _rm_workdir(self):
        try:
            shutil.rmtree(workdir)
        except OSError:
            pass

    def setUp(self):
        self._rm_workdir()
        os.mkdir(workdir)
        os.chdir(workdir)

    def tearDown(self):
        os.chdir(self.oldpwd)
        self._rm_workdir()

    @staticmethod
    def get_json_data(name):
        with open(os.path.join(testdir, 'data', name)) as fo:
            return json.load(fo)

class DBTest(AbstractTest):
    def __init__(self, *args, **kwargs):
        super(DBTest, self).__init__(*args, **kwargs)
        self.s = None
        self.task_id_counter = 1
        self.collection = m.Collection(name="foo", display_name="bar", target_tag="tag",
                                       build_tag="tag2", priority_coefficient=1.0)

    def setUp(self):
        super(DBTest, self).setUp()
        tables = m.Base.metadata.tables
        conn = m.engine.connect()
        for table in tables.values():
            conn.execute(table.delete())
        conn.close()
        self.s = m.Session()
        self.s.add(self.collection)
        self.s.commit()

    def tearDown(self):
        super(DBTest, self).tearDown()
        self.s.close()

    def prepare_basic_data(self):
        pkg = m.Package(name='rnv', collection_id=self.collection.id)
        self.s.add(pkg)
        self.s.flush()
        build = m.Build(package_id=pkg.id, state=m.Build.RUNNING,
                        task_id=666)
        self.s.add(build)
        self.s.commit()
        return pkg, build

    def prepare_packages(self, pkg_names):
        pkgs = []
        for name in pkg_names:
            pkg = self.s.query(m.Package).filter_by(name=name).first()
            if not pkg:
                pkg = m.Package(name=name, collection_id=self.collection.id)
                self.s.add(pkg)
            pkgs.append(pkg)
        self.s.commit()
        return pkgs

    def prepare_builds(self, repo_id=1, **builds):
        new_builds = []
        for pkg_name, state in sorted(builds.items()):
            states = {
                True: m.Build.COMPLETE,
                False: m.Build.FAILED,
                None: m.Build.RUNNING,
            }
            if isinstance(state, bool):
                state = states[state]
            package_id = self.s.query(m.Package.id).filter_by(name=pkg_name).scalar()
            build = m.Build(package_id=package_id, state=state, repo_id=repo_id,
                            task_id=self.task_id_counter)
            self.task_id_counter += 1
            self.s.add(build)
            new_builds.append(build)
        self.s.commit()
        return new_builds

    @staticmethod
    def parse_pkg(string):
        epoch = None
        if ':' in string:
            epoch, _, string = string.partition(':')
        name, version, release = string.rsplit('-', 2)
        return dict(epoch=epoch, name=name, version=version, release=release,
                    arch='x86_64')

class KojiMock(Mock):
    def __init__(self, *args, **kwargs):
        Mock.__init__(self, *args, **kwargs)
        self.koji_id = 'primary'
        self.multicall = False
        self._mcall_list = []

    def __getattribute__(self, key):
        if (key.lower() != 'multicall' and
                not key.startswith('_') and
                object.__getattribute__(self, 'multicall')):
            def mcall_method(*args, **kwargs):
                object.__getattribute__(self, '_mcall_list').append((key, args, kwargs))
            return mcall_method
        return Mock.__getattribute__(self, key)

    def multiCall(self):
        self.multicall = False
        ret = [[getattr(self, key)(*args, **kwargs)]
               for key, args, kwargs in self._mcall_list]
        self._mcall_list = []
        return ret
