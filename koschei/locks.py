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

from contextlib import contextmanager

from sqlalchemy.sql import func


LOCK_REPO_RESOLVER = 1
LOCK_BUILD_RESOLVER = 2


class Locked(Exception):
    pass


def pg_lock(db, namespace, key, block=True, transaction=False, shared=False):
    """
    Lock an arbitrary resource identified by an integer key using PostgreSQL
    advisory lock.

    :param: db Database session
    :param: namespace Integer namespace identifier. Should use one of the constants
            defined in this module.
    :param: key Integer identifier of the lock.
    :param: block Whether to block waiting for the lock.
    :param: transaction Whether the lock should be scoped by the transaction
            (unlocks when the transaction ends). Otherwise scoped by the
            session.
    :param: shared Whether the lock should be shared. Otherwise it is
            exclusive.

    :raises: Locked if in non blocking mode and failed to obtain the lock.

    Exact semantics are described in PostgreSQL documentation:
    https://www.postgresql.org/docs/9.2/static/explicit-locking.html#ADVISORY-LOCKS
    https://www.postgresql.org/docs/9.2/static/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
    """
    fn_name = 'pg_'
    if not block:
        fn_name += 'try_'
    fn_name += 'advisory_'
    if transaction:
        fn_name += 'xact_'
    fn_name += 'lock'
    if shared:
        fn_name += '_shared'
    function = getattr(func, fn_name)
    res = db.query(function(namespace, key)).scalar()
    if not block and not res:
        raise Locked()


def pg_unlock(db, namespace, key, shared=False):
    """
    Unlocks given advisory session lock. Arguments have the same meaning as in pg_lock
    """
    fn_name = 'pg_advisory_unlock'
    if shared:
        fn_name += '_shared'
    function = getattr(func, fn_name)
    db.query(function(namespace, key)).one()


def pg_unlock_all(db):
    """
    Unlocks advisory session locks.
    """
    db.query(func.pg_advisory_unlock_all()).one()


@contextmanager
def pg_session_lock(db, namespace, key, block=True, shared=False):
    """
    Context manager for obtaining a session lock.
    With block=True (default) blocks until the resource is locked.
    """
    locked = False
    try:
        pg_lock(db, namespace, key, block=block, shared=shared)
        locked = True
        yield
    finally:
        if locked:
            pg_unlock(db, namespace, key, shared=shared)
