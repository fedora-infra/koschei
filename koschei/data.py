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

from sqlalchemy import insert

from koschei.db import get_or_create
from koschei.models import (
    Package, BasePackage, PackageGroupRelation, GroupACL, User, Collection,
    CollectionGroupRelation, Build, AppliedChange, KojiTask, ResolutionChange,
    ResolutionProblem,
)


class PackagesDontExist(Exception):
    def __init__(self, packages):
        super(PackagesDontExist, self).__init__(self)
        self.packages = packages

    def __str__(self):
        return "Packages don't exist: " + ','.join(self.packages)


def track_packages(session, collection, package_names):
    """
    Sets given packages as tracked within collection.

    :param: session koschei session
    :param: collection Collection ORM object where packages should be added
    :param: package_names list of package names to track
    :raises: PackagesDontExist if some of the packages cannot be found
    :return: a list of Package objects that were newly tracked
    """
    package_names = set(package_names)
    existing = session.db.query(Package)\
        .filter(Package.name.in_(package_names))\
        .filter(Package.collection_id == collection.id)
    nonexistent = package_names - {p.name for p in existing}
    if nonexistent:
        raise PackagesDontExist(nonexistent)
    to_add = [p for p in existing if not p.tracked]
    for package in to_add:
        set_package_attribute(session, package, 'tracked', True)
    return to_add


def set_package_attribute(session, package, attr, new_value):
    """
    Sets given package attribute, such as manual_priority, and logs a new user
    action if needed.
    """
    prev = getattr(package, attr)
    if prev != new_value:
        session.log_user_action(
            "Package {} (collection {}): {} set from {} to {}"
            .format(package.name, package.collection.name, attr, prev, new_value),
            base_id=package.base_id,
        )
        setattr(package, attr, new_value)


def set_group_content(session, group, packages, append=False, delete=False):
    """
    Makes given group contain given packages (by name).
    In append mode (append=True) doesn't remove any packages from the group.
    With append=False, makes the group contain only specified packages.
    With delete=True, only deletes given packages from the group.

    :param: session koschei session
    :param: group PackageGroup object
    :param: packages list of package names to be in given group
    :param: append whether to clear the group first or append to existing content
    :param: delete whether to delete instead of adding
    :raises: PackagesDontExist when packages weren't found
    """
    assert not append or not delete
    contents = set(packages)
    new_content = set(
        session.db.query(BasePackage)
        .filter(BasePackage.name.in_(contents))
        .all()
    )
    if len(new_content) != len(contents):
        raise PackagesDontExist(contents - {base.name for base in new_content})
    current_content = set(
        session.db.query(BasePackage)
        .join(PackageGroupRelation)
        .filter(PackageGroupRelation.group_id == group.id)
        .all()
    )
    if delete:
        to_add = set()
    else:
        to_add = new_content - current_content
    if to_add:
        rels = [dict(group_id=group.id, base_id=base.id) for base in to_add]
        session.db.execute(insert(PackageGroupRelation, rels))
        for base in to_add:
            session.log_user_action(
                "Group {} modified: package {} added".format(group.name, base.name),
                base_id=base.id,
            )
    if append:
        to_delete = set()
    elif delete:
        to_delete = new_content
    else:
        to_delete = current_content - new_content
    if to_delete:
        (
            session.db.query(PackageGroupRelation)
            .filter(PackageGroupRelation.group_id == group.id)
            .filter(PackageGroupRelation.base_id.in_(base.id for base in to_delete))
            .delete()
        )
        for base in to_delete:
            session.log_user_action(
                "Group {} modified: package {} removed".format(group.name, base.name),
                base_id=base.id,
            )


def set_group_maintainers(session, group, maintainers):
    """
    Sets given group maintaners to given list of names.

    :param: session koschei session
    :param: group PackageGroup object
    :param: maintainers list of maintainer names
    """
    current_users = set(
        session.db.query(User)
        .join(GroupACL)
        .filter(GroupACL.group_id == group.id)
        .all()
    )
    new_users = {get_or_create(session.db, User, name=name) for name in set(maintainers)}
    session.db.flush()
    to_add = new_users - current_users
    if to_add:
        session.db.execute(
            insert(GroupACL),
            [dict(group_id=group.id, user_id=user.id) for user in to_add],
        )
        for user in to_add:
            session.log_user_action(
                "Group {} modified: maintainer {} added".format(group.name, user.name)
            )
    to_delete = current_users - new_users
    if to_delete:
        (
            session.db.query(GroupACL)
            .filter(GroupACL.group_id == group.id)
            .filter(GroupACL.user_id.in_(user.id for user in to_delete))
            .delete()
        )
        for user in to_delete:
            session.log_user_action(
                "Group {} modified: maintainer {} removed".format(group.name, user.name)
            )


def delete_group(session, group):
    """
    Deletes given package group

    :param: session koschei session
    :param: group PackageGroup object
    """
    session.log_user_action("Group {} deleted".format(group.name))
    session.db.delete(group)


def set_collection_group_content(session, group, collection_names):
    """
    Makes given collection group contain given collections

    :param: session koschei session
    :param: group collection group
    :param: collection_names list of collection names
    """
    collection_names = set(collection_names)
    collection_ids = session.db.query(Collection.id)\
        .filter(Collection.name.in_(collection_names))\
        .all_flat()
    if len(collection_ids) != len(collection_names):
        raise RuntimeError("Some collections weren't found")
    rels = [dict(group_id=group.id, collection_id=coll_id)
            for coll_id in collection_ids]
    session.db.query(CollectionGroupRelation)\
        .filter(CollectionGroupRelation.group_id == group.id)\
        .delete()
    if rels:
        session.db.execute(insert(CollectionGroupRelation, rels))


def copy_collection(session, source, copy):

    def get_cols(entity, exclude=(), qualify=False):
        return ', '.join(
            (entity.__tablename__ + '.' + c.name if qualify else c.name)
            for c in entity.__table__.columns
            if c.name != 'id' and c.name not in exclude
        )

    def deepcopy_table(entity, whereclause='', foreign_keys=None):
        if not foreign_keys:
            foreign_keys = entity.__table__.foreign_keys
        assert len(foreign_keys) == 1
        foreign_key = next(iter(foreign_keys))
        parent = foreign_key.column.table
        fk_cols = [fk.parent.name for fk in foreign_keys if fk.column.table is parent]
        assert len(fk_cols) == 1
        fk_col = fk_cols[0]
        session.log.info("Copying {} table".format(entity.__tablename__))
        session.db.execute("""
            CREATE TEMPORARY TABLE {table}_copy AS
                SELECT {table}.id AS original_id,
                        nextval('{table}_id_seq') AS id,
                        {parent}.id AS {fk_col}
                    FROM {table} JOIN {parent}_copy AS {parent}
                            ON {fk_col} = {parent}.original_id
                    {whereclause}
                    ORDER BY {table}.id;
            INSERT INTO {table}(id, {fk_col}, {cols})
                SELECT {table}_copy.id AS id,
                       {table}_copy.{fk_col} AS {fk_col},
                       {cols_q}
                    FROM {table} JOIN {table}_copy
                        ON {table}.id = {table}_copy.original_id
                    ORDER BY {table}_copy.original_id;
        """.format(
            table=entity.__tablename__,
            parent=parent.name,
            fk_col=fk_col,
            cols=get_cols(entity, exclude=[fk_col]),
            cols_q=get_cols(entity, exclude=[fk_col], qualify=True),
            whereclause=whereclause,
        ))

    session.log.info("Copying package table")
    session.db.execute("""
        CREATE TEMPORARY TABLE package_copy AS
            SELECT id AS original_id,
                    nextval('package_id_seq') AS id
                FROM package
                WHERE collection_id = {source.id};
        INSERT INTO package(id, collection_id, {package_cols})
            SELECT package_copy.id AS id,
                   {copy.id} AS collection_id,
                   {package_cols_q}
                FROM package JOIN package_copy
                    ON package.id = package_copy.original_id;

        -- NOTE: package.last_[complete_]build is updated by trigger
    """.format(
        copy=copy, source=source,
        package_cols=get_cols(Package, exclude=['collection_id']),
        package_cols_q=get_cols(Package, exclude=['collection_id'], qualify=True),
    ))

    deepcopy_table(
        Build,
        whereclause="""
            WHERE started > now() - '1 month'::interval
                  OR build.id IN (
                        SELECT last_complete_build_id
                        FROM package
                        WHERE collection_id = {copy.id}
                  )
        """.format(copy=copy),
    )

    deepcopy_table(KojiTask)
    deepcopy_table(ResolutionChange)
    deepcopy_table(ResolutionProblem)
    deepcopy_table(
        AppliedChange,
        foreign_keys=AppliedChange.__table__.c.build_id.foreign_keys,
    )
