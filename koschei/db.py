# Copyright (C) 2016  Red Hat, Inc.
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

from __future__ import print_function, absolute_import

import re
import os
import struct
import zlib
import six

import sqlalchemy

from sqlalchemy import create_engine, Table, DDL
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.orm import sessionmaker, CompositeProperty
from sqlalchemy.engine.url import URL
from sqlalchemy.event import listen
from sqlalchemy.types import TypeDecorator
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import BYTEA

from . import util
from .config import get_config


class Base(object):
    # pylint: disable=no-self-argument,no-member
    @declared_attr
    def __tablename__(cls):
        return re.sub(r'([A-Z]+)', lambda s: '_' + s.group(0).lower(), cls.__name__)[1:]

Base = declarative_base(cls=Base)


class Query(sqlalchemy.orm.Query):
    # TODO move get_or_create here
    # def get_or_create(self):
    #     items = self.all()
    #     if items:
    #         if len(items) > 1:
    #             raise ProgrammerError("get_or_create query returned more than one item")
    #         return items[0]
    #     entity = self._primary_entity.entities[0]
    #     how to get args?
    #     self.session.add(entity(**args))

    def delete(self, *args, **kwargs):
        kwargs['synchronize_session'] = False
        return super(Query, self).delete(*args, **kwargs)

    def update(self, *args, **kwargs):
        kwargs['synchronize_session'] = False
        return super(Query, self).update(*args, **kwargs)

    def lock_rows(self):
        """
        Locks rows satisfying given filter expression in consistent order.
        Should eliminate deadlocks with other transactions that do the same before
        attempting to update.
        """
        mapper = self._only_full_mapper_zero("lock_rows")
        self.order_by(*mapper.primary_key)\
            .with_lockmode('update')\
            .all()

    def all_flat(self, ctor=list):
        return ctor(x for [x] in self)


class KoscheiDbSession(sqlalchemy.orm.session.Session):
    def bulk_insert(self, objects):
        """
        Inserts ORM objects using sqla-core bulk insert. Only handles simple flat
        objects, no relationships. Assumes the primary key is generated
        sequence and the attribute is named "id". Sets object ids.

        :param: objects List of ORM objects to be persisted. All objects must be of
                        the same type. Column list is determined from first object.
        """
        # pylint:disable=unidiomatic-typecheck
        if objects:
            cls = type(objects[0])
            table = cls.__table__
            cols = [col for col in objects[0].__dict__.keys() if not
                    col.startswith('_') and col != 'id']
            dicts = []
            for obj in objects:
                assert type(obj) == cls
                dicts.append({col: getattr(obj, col) for col in cols})
            self.flush()
            res = self.execute(table.insert(dicts, returning=[table.c.id]))
            ids = sorted(x[0] for x in res)
            for obj, obj_id in zip(objects, ids):
                obj.id = obj_id
            self.expire_all()


__engine = None
__sessionmaker = None


def get_engine():
    global __engine
    if __engine:
        return __engine
    db_url = get_config('db_url', None) or URL(**get_config('database_config'))
    __engine = create_engine(db_url, echo=False, pool_size=10)
    return __engine


def get_sessionmaker():
    global __sessionmaker
    if __sessionmaker:
        return __sessionmaker
    __sessionmaker = sessionmaker(bind=get_engine(), autocommit=False,
                                  class_=KoscheiDbSession, query_cls=Query)
    return __sessionmaker


def Session(*args, **kwargs):
    return get_sessionmaker()(*args, **kwargs)


def get_or_create(db, table, **cond):
    """
    Returns a row from table that satisfies cond or a new row if no such row
    exists yet. Can still cause IntegrityError in concurrent environment.
    """
    item = db.query(table).filter_by(**cond).first()
    if not item:
        item = table(**cond)
        db.add(item)
    return item


class CompressedKeyArray(TypeDecorator):
    impl = BYTEA

    def _compress(self, payload):
        if six.PY2:
            payload = str(payload)
        return zlib.compress(payload)

    def _decompress(self, compressed):
        payload = zlib.decompress(compressed)
        if six.PY2:
            payload = str(payload)
        return payload

    def process_bind_param(self, value, _):
        if value is None:
            return None
        value = sorted(value)
        offset = 0
        for i in range(len(value)):
            value[i] -= offset
            offset += value[i]
        array = bytearray()
        for item in value:
            assert item > 0
            array += struct.pack(">I", item)
        return self._compress(array)

    def process_result_value(self, value, _):
        if value is None:
            return None
        res = []
        uncompressed = self._decompress(value)
        for i in range(0, len(uncompressed), 4):
            res.append(struct.unpack(">I", uncompressed[i:i + 4])[0])
        offset = 0
        for i in range(len(res)):
            res[i] += offset
            offset = res[i]
        return res


def load_ddl():
    for script in ('triggers.sql', 'rpmvercmp.sql'):
        with open(os.path.join(get_config('directories.datadir'), script)) as ddl_script:
            ddl = DDL(ddl_script.read())
        listen(Base.metadata, 'after_create', ddl.execute_if(dialect='postgresql'))


def grant_db_access(_, conn, *args, **kwargs):
    user = get_config('unpriv_db_username', None)
    if user:
        conn.execute("""
                     GRANT SELECT, INSERT, UPDATE, DELETE
                     ON ALL TABLES IN SCHEMA PUBLIC TO {user};
                     GRANT SELECT, USAGE ON ALL SEQUENCES
                     IN SCHEMA PUBLIC TO {user};
                     """.format(user=user))


listen(Table, 'after_create', grant_db_access)


def create_all():
    load_ddl()
    Base.metadata.create_all(get_engine())


class CmpMixin(object):
    def __eq__(self, other):
        return self._cmp(other) == 0

    def __ne__(self, other):
        return self._cmp(other) != 0

    def __lt__(self, other):
        return self._cmp(other) < 0

    def __le__(self, other):
        return self._cmp(other) <= 0

    def __gt__(self, other):
        return self._cmp(other) > 0

    def __ge__(self, other):
        return self._cmp(other) >= 0


class RpmEVR(CmpMixin):
    def __init__(self, epoch, version, release):
        self.epoch = epoch
        self.version = version
        self.release = release

    def __composite_values__(self):
        return self.epoch, self.version, self.release

    def __repr__(self):
        return 'EVR({}:{}-{})'.format(self.epoch or 0, self.version, self.release)

    def __str__(self):
        epoch, version, release = self.__composite_values__()
        if not version or not release:
            return ''
        if len(release) > 16:
            release = release[:13] + '...'
        if epoch:
            return '{}:{}-{}'.format(epoch, version, release)
        return '{}-{}'.format(version, release)

    def _cmp(self, other):
        if not isinstance(other, self.__class__):
            return NotImplemented
        return util.compare_evr(self.__composite_values__(), other.__composite_values__())


class RpmEVRComparator(CmpMixin, CompositeProperty.Comparator):
    def _cmp(self, other):
        evr = self.__clause_element__().clauses
        return func.rpmvercmp_evr(
            evr[0], evr[1], evr[2],
            other.epoch, other.version, other.release,
        )
