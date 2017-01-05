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
from koschei.models import (Package, BasePackage, PackageGroup,
                            PackageGroupRelation, GroupACL, User,
                            Collection, CollectionGroupRelation)


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
    if to_add:
        session.db.query(Package)\
            .filter(Package.id.in_(p.id for p in to_add))\
            .update({'tracked': True})
    return to_add


def set_group_content(session, group, packages, append=False):
    """
    Makes given group contain given packages (by name).
    In append mode (append=True) doesn't remove any packages from the group.
    With append=False, makes the group contain only specified packages.

    :param: session koschei session
    :param: group PackageGroup object
    :param: packages list of package names to be in given group
    :param: append whether to clear the group first or append to existing content
    :raises: PackagesDontExist when packages weren't found
    """
    contents = set(packages)
    bases = session.db.query(BasePackage.id, BasePackage.name)\
        .filter(BasePackage.name.in_(contents))\
        .all()
    if len(bases) != len(contents):
        raise PackagesDontExist(contents - {base.name for base in bases})
    base_ids = {base.id for base in bases}
    if append:
        base_ids -= session.db.query(PackageGroupRelation.base_id)\
            .filter(PackageGroup.id == group.id)\
            .filter(PackageGroupRelation.base_id.in_(base_ids))\
            .all_flat(set)
    rels = [dict(group_id=group.id, base_id=base_id) for base_id in base_ids]
    if not append:
        session.db.query(PackageGroupRelation).filter_by(group_id=group.id).delete()
    if rels:
        session.db.execute(insert(PackageGroupRelation, rels))


def set_group_maintainers(session, group, maintainers):
    """
    Sets given group maintaners to given list of names.

    :param: session koschei session
    :param: group PackageGroup object
    :param: maintainers list of maintainer names
    """
    users = [get_or_create(session.db, User, name=name) for name in set(maintainers)]
    session.db.query(GroupACL)\
        .filter(GroupACL.group_id == group.id)\
        .delete()
    acls = [dict(group_id=group.id, user_id=user.id)
            for user in users]
    session.db.execute(insert(GroupACL), acls)


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
