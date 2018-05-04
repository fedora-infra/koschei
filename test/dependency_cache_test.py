# Copyright (C) 2018  Red Hat, Inc.
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

from test.common import DBTest
from koschei.backend.services.resolver import DependencyCache
from koschei.models import Dependency


# pylint:disable=unbalanced-tuple-unpacking
class DependencyCacheTest(DBTest):
    def dep(self, i):
        return (i, 'foo', 0, str(i), '1', 'x86_64')

    def nevra(self, i):
        return ('foo', 0, str(i), '1', 'x86_64')

    def setUp(self):
        super(DependencyCacheTest, self).setUp()
        deps = [str(self.nevra(i)) for i in range(1, 4)]
        self.db.execute(
            "INSERT INTO dependency(name,epoch,version,release,arch) VALUES {}"
            .format(','.join(deps)))
        self.db.commit()

    def test_get_ids(self):
        cache = DependencyCache(self.db, 10)
        # from db
        dep1, dep2 = cache.get_by_ids([2, 3])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # from cache
        cache.db = None
        dep1, dep2 = cache.get_by_ids([2, 3])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        cache.db = self.db
        # mixed
        dep1, dep2 = cache.get_by_ids([2, 1])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(1), dep2)
        hash(dep1)
        hash(dep2)

    def test_get_nevras(self):
        cache = DependencyCache(self.db, 10)
        # from db
        dep1, dep2 = cache.get_or_create_nevras([self.nevra(2), self.nevra(3)])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        # from cache
        cache.db = None
        dep1, dep2 = cache.get_or_create_nevras([self.nevra(2), self.nevra(3)])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(3), dep2)
        hash(dep1)
        hash(dep2)
        cache.db = self.db
        # insert
        dep1, dep2 = cache.get_or_create_nevras([self.nevra(4), self.nevra(5)])
        self.assertEqual(self.dep(4), dep1)
        self.assertEqual(self.dep(5), dep2)
        hash(dep1)
        hash(dep2)
        # mixed
        dep1, dep2, dep3 = cache.get_or_create_nevras(
            [self.nevra(6), self.nevra(4), self.nevra(2)]
        )
        self.assertEqual(self.dep(6), dep1)
        self.assertEqual(self.dep(4), dep2)
        self.assertEqual(self.dep(2), dep3)
        hash(dep1)
        hash(dep2)
        hash(dep3)

        # now get them by id
        dep1, dep2, dep3 = cache.get_by_ids([dep1.id, dep2.id, dep3.id])
        self.assertEqual(self.dep(6), dep1)
        self.assertEqual(self.dep(4), dep2)
        self.assertEqual(self.dep(2), dep3)
        hash(dep1)
        hash(dep2)
        hash(dep3)

    def test_lru(self):
        cache = DependencyCache(self.db, 2)
        # from db
        cache.get_by_ids([2, 3])
        # one more
        cache.get_by_ids([1])
        # from cache
        cache.db = None
        cache.get_by_ids([3])
        self.db.query(Dependency).filter_by(version="2").update({'name': 'bar'})
        self.db.commit()
        cache.db = self.db
        # refetch
        dep = cache.get_by_ids([2])[0]
        self.assertEqual('bar', dep.name)
        # still cached
        cache.db = None
        cache.get_by_ids([3])

    def test_lru2(self):
        cache = DependencyCache(self.db, 2)
        # from db
        dep1, dep2 = cache.get_or_create_nevras([self.nevra(2), self.nevra(3)])
        self.assertEqual(self.dep(2), dep1)
        self.assertEqual(self.dep(3), dep2)
        # one mode
        dep1 = cache.get_or_create_nevras([self.nevra(1)])[0]
        self.assertEqual(self.dep(1), dep1)
        hash(dep1)
        # from cache
        cache.db = None
        dep3 = cache.get_or_create_nevras([self.nevra(3)])[0]
        self.assertEqual(self.dep(3), dep3)
        hash(dep3)
        cache.db = self.db

        self.db.query(Dependency).filter_by(version="2").update({'name': 'bar'})
        self.db.commit()
        # 2 should be expired, this should insert new
        dep = cache.get_or_create_nevras([self.nevra(2)])[0]
        self.assertEqual('foo', dep.name)
        hash(dep)
        # still cached
        cache.db = None
        dep3 = cache.get_or_create_nevras([self.nevra(3)])[0]
        self.assertEqual(self.dep(3), dep3)
        hash(dep3)
