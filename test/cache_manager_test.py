# Copyright (C) 2015 Red Hat, Inc.
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

from unittest import TestCase
from time import sleep
from koschei.cache_manager import CacheManager

class RepoFactory(object):
    def create(self, repo_id, ignored):
        return "repo %s" % repo_id
    def destroy(self, repo_id, repo):
        assert repo == ("repo %s" % repo_id)
    def populate_cache(self):
        pass

class SackFactory(object):
    def create(self, repo_id, repo):
        assert repo == ("repo %s" % repo_id)
        return "sack %s" % repo_id
    def destroy(self, repo_id, sack):
        assert sack == ("sack %s" % repo_id)
    def populate_cache(self):
        pass

class CacheManagerTest(TestCase):
    def setUp(self):
        self.mgr = CacheManager(2)
        self.mgr.add_bank(SackFactory(), 3, 2)
        self.mgr.add_bank(RepoFactory(), 5, 4)

    def test_termination(self):
        self.mgr.terminate()

    def test_single_item(self):
	self.mgr.prefetch(7541)
	self.mgr.acquire(7541)
	self.mgr.release(7541)
	self.mgr.terminate()

    def test_double_item(self):
	self.mgr.prefetch(7541)
	self.mgr.prefetch(689)
	self.mgr.acquire(7541)
	self.mgr.acquire(689)
	self.mgr.release(7541)
	self.mgr.release(689)
	self.mgr.terminate()

    def test_acquire_cached(self):
	self.mgr.prefetch(7541)
	self.mgr.acquire(7541)
	self.mgr.release(7541)
	self.mgr.prefetch(7541)
	self.mgr.acquire(7541)
	self.mgr.release(7541)
	self.mgr.terminate()

    def test_acquire_multiple(self):
	for i in xrange(1, 10):
	    self.mgr.prefetch(i)
	sleep(1)
	for i in xrange(1, 10):
	    self.mgr.acquire(i)
	    sleep(0.05)
	    self.mgr.release(i)
	self.mgr.terminate()
