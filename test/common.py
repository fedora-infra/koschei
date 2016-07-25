# Copyright (C) 2014-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from __future__ import print_function

import os
import sys
import unittest
import shutil
import json
import psycopg2

from mock import Mock

from test import testdir, config
from koschei.models import (get_engine, Base, Session, Package, Build,
                            Collection, BasePackage, PackageGroupRelation,
                            PackageGroup, GroupACL, User)

workdir = '.workdir'


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
    POSTGRES_OPTS = {
        # Enable faster, but unsafe operation.
        # (COMMIT statements return before WAL is written to disk.)
        'synchronous_commit': 'off',
        # Log all SQL statements.
        'log_statement': 'all',
        # Use sequential scanning only when absolutely necessary. Also enable logging
        # of statement execution plans. With these two options combined, logs can be
        # searched for sequential scans to find missing indexes.
        'enable_seqscan': 'off',
        'debug_print_plan': 'on',
    }
    postgres_initialized = None

    @staticmethod
    def init_postgres():
        print("Initializing test database...", file=sys.stderr)
        dbname = config['database_config']['database']
        with psycopg2.connect(dbname='postgres') as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP DATABASE IF EXISTS {0}".format(dbname))
                cur.execute("CREATE DATABASE {0}".format(dbname))
                for option, value in DBTest.POSTGRES_OPTS.iteritems():
                    cur.execute("ALTER DATABASE {0} SET {1} TO '{2}'".format(dbname,
                                                                             option,
                                                                             value))
        Base.metadata.create_all(get_engine())

    def __init__(self, *args, **kwargs):
        super(DBTest, self).__init__(*args, **kwargs)
        self.db = None
        self.task_id_counter = 1
        self.collection = Collection(
            name="f25", display_name="Fedora Rawhide", target="f25",
            dest_tag='f25', build_tag="f25-build", priority_coefficient=1.0,
            latest_repo_resolved=True, latest_repo_id=123,
        )

    @classmethod
    def setUpClass(cls):
        super(DBTest, cls).setUpClass()
        if DBTest.postgres_initialized is None:
            DBTest.postgres_initialized = False
            if not os.environ.get('TEST_WITHOUT_POSTGRES'):
                DBTest.init_postgres()
                DBTest.postgres_initialized = True

    def setUp(self):
        if not DBTest.postgres_initialized:
            self.skipTest("requires PostgreSQL")
        super(DBTest, self).setUp()
        tables = Base.metadata.tables
        conn = get_engine().connect()
        for table in tables.values():
            conn.execute(table.delete())
            if hasattr(table.c, 'id'):
                conn.execute("ALTER SEQUENCE {}_id_seq RESTART".format(table.name))
        conn.close()
        self.db = Session()
        self.db.add(self.collection)
        self.db.commit()

    def tearDown(self):
        super(DBTest, self).tearDown()
        self.db.close()

    def ensure_base_package(self, package):
        if not package.base_id:
            base = self.db.query(BasePackage).filter_by(name=package.name).first()
            if not base:
                base = BasePackage(name=package.name)
                self.db.add(base)
                self.db.flush()
            package.base_id = base.id

    def prepare_basic_data(self):
        pkg = Package(name='rnv', collection_id=self.collection.id)
        self.ensure_base_package(pkg)
        self.db.add(pkg)
        self.db.flush()
        build = Build(package_id=pkg.id, state=Build.RUNNING,
                      task_id=666, repo_id=1)
        self.db.add(build)
        self.db.commit()
        return pkg, build

    def prepare_packages(self, pkg_names):
        pkgs = []
        for name in pkg_names:
            pkg = self.db.query(Package).filter_by(name=name).first()
            if not pkg:
                pkg = Package(name=name, collection_id=self.collection.id)
                self.ensure_base_package(pkg)
                self.db.add(pkg)
            pkgs.append(pkg)
        self.db.commit()
        return pkgs

    def prepare_build(self, pkg_name, state=None, repo_id=None, resolved=True):
        states = {
            True: Build.COMPLETE,
            False: Build.FAILED,
            None: Build.RUNNING,
        }
        if isinstance(state, bool):
            state = states[state]
        self.prepare_packages([pkg_name])
        package_id = self.db.query(Package.id).filter_by(name=pkg_name).scalar()
        build = Build(package_id=package_id, state=state,
                      repo_id=repo_id or (1 if state != Build.RUNNING else None),
                      version='1', release='1.fc25',
                      task_id=self.task_id_counter,
                      deps_resolved=resolved)
        self.task_id_counter += 1
        self.db.add(build)
        self.db.commit()
        return build

    def prepare_user(self, **kwargs):
        user = self.db.query(User).filter_by(**kwargs).first()
        if user:
            return user
        user = User(**kwargs)
        self.db.add(user)
        self.db.commit()
        return user

    def prepare_group(self, name, content=(), namespace=None, owners=('john.doe',)):
        users = [self.prepare_user(name=name) for name in owners]
        packages = self.prepare_packages(content)
        group = PackageGroup(name=name, namespace=namespace)
        self.db.add(group)
        self.db.commit()
        self.db.execute(PackageGroupRelation.__table__.insert(),
                        [dict(group_id=group.id, base_id=package.base_id)
                         for package in packages])
        self.db.execute(GroupACL.__table__.insert(),
                        [dict(group_id=group.id, user_id=user.id)
                         for user in users])
        self.db.commit()
        return group


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
