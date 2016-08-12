#!/usr/bin/python
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

# pylint: disable=W0221
import re
import os
import sys
import argparse

from koschei import data
from koschei.models import (get_engine, Base, Package, PackageGroup, Session,
                            AdminNotice, Collection, CollectionGroup)
from koschei.backend import koji_util, Backend
from koschei.config import load_config, get_config


load_config(['/usr/share/koschei/config.cfg',
             '/etc/koschei/config-backend.cfg',
             '/etc/koschei/config-admin.cfg'])


class Command(object):
    needs_db = True
    needs_koji = False
    needs_koji_login = False

    def setup_parser(self, parser):
        pass

    def execute(self, **kwargs):
        raise NotImplementedError()


def main():
    main_parser = argparse.ArgumentParser()
    subparser = main_parser.add_subparsers()
    for Cmd in Command.__subclasses__():
        cmd_name = re.sub(r'([A-Z])', lambda s: '-' + s.group(0).lower(),
                          Cmd.__name__)[1:]
        cmd = Cmd()
        parser = subparser.add_parser(cmd_name, help=cmd.__doc__)
        cmd.setup_parser(parser)
        parser.set_defaults(cmd=cmd)
    args = main_parser.parse_args()
    cmd = args.cmd
    kwargs = vars(args)
    del kwargs['cmd']
    if cmd.needs_db:
        kwargs['db'] = db = Session()
    if cmd.needs_koji or cmd.needs_koji_login:
        primary = koji_util.KojiSession(anonymous=not cmd.needs_koji_login)
        secondary = koji_util.KojiSession(anonymous=True, koji_id='secondary')
        kwargs['koji_sessions'] = {'primary': primary, 'secondary': secondary}
    cmd.execute(**kwargs)
    if cmd.needs_db:
        db.commit()
        db.close()


class CreateDb(Command):
    """ Creates database tables """

    needs_db = False

    def execute(self):
        from alembic.config import Config
        from alembic import command
        Base.metadata.create_all(get_engine())
        alembic_cfg = Config(get_config('alembic.alembic_ini'))
        command.stamp(alembic_cfg, "head")


class Cleanup(Command):
    """ Cleans old builds from the database """

    def setup_parser(self, parser):
        parser.add_argument('--older-than', type=int,
                            help="Delete builds older than N months",
                            default=6)
        parser.add_argument('--reindex', action='store_true')
        parser.add_argument('--vacuum', action='store_true')

    def execute(self, db, older_than, reindex, vacuum):
        if older_than < 2:
            sys.exit("Minimal allowed value is 2 months")
        conn = db.connection()
        conn.connection.connection.rollback()
        conn.connection.connection.autocommit = True
        res = conn.execute(
            """DELETE FROM build WHERE started < now() - '{months} month'::interval
            AND id NOT IN (SELECT last_complete_build_id FROM package
                           WHERE last_complete_build_id IS NOT NULL)
            """.format(months=older_than))
        print("Deleted {} builds".format(res.rowcount))
        res.close()
        if vacuum:
            conn.execute("VACUUM FULL ANALYZE")
            print("Vacuum full analyze done")
        if reindex:
            conn.execute("REINDEX DATABASE {}".format(get_engine().url.database))
            print("Reindexed")


class SetNotice(Command):
    """ Set admin notice displayed in web interface """

    def setup_parser(self, parser):
        parser.add_argument('content')

    def execute(self, db, content):
        key = 'global_notice'
        content = content.strip()
        notice = db.query(AdminNotice).filter_by(key=key).first()
        notice = notice or AdminNotice(key=key)
        notice.content = content
        db.add(notice)


class ClearNotice(Command):
    """ Clears current admin notice """

    def execute(self, db):
        key = 'global_notice'
        db.query(AdminNotice).filter_by(key=key).delete()


class AddPkg(Command):
    """ Adds given packages to database """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('-c', '--collection', required=True)

    def execute(self, db, names, collection):
        if collection:
            collection = db.query(Collection)\
                .filter_by(name=collection)\
                .first()
            if not collection:
                sys.exit("Collection not found")
        try:
            data.track_packages(db, collection, names)
        except data.PackagesDontExist as e:
            sys.exit(str(e))


class AddGroup(Command):

    def setup_parser(self, parser):
        parser.add_argument('group')
        parser.add_argument('pkgs', nargs='*')
        parser.add_argument('-m', '--maintainer', nargs='*')

    def execute(self, db, group, pkgs, maintainer):
        namespace, name = PackageGroup.parse_name(group)
        if (db.query(PackageGroup)
                .filter_by(namespace=namespace, name=name)
                .count()):
            sys.exit("Group already exists")
        group_obj = PackageGroup(name=name, namespace=namespace)
        db.add(group_obj)
        db.flush()
        try:
            data.set_group_content(db, group, pkgs)
            data.set_group_maintainers(db, group, maintainer)
        except data.PackagesDontExist as e:
            sys.exit(str(e))


class SetPriority(Command):
    """ Sets package's priority to given value """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('value')
        parser.add_argument('--static', action='store_true')

    def execute(self, db, names, value, static):
        pkgs = db.query(Package)\
            .filter(Package.name.in_(names)).all()
        if len(names) != len(pkgs):
            not_found = set(names).difference(pkg.name for pkg in pkgs)
            sys.exit('Packages not found: {}'.format(','.join(not_found)))
        for pkg in pkgs:
            if static:
                pkg.static_priority = value
            else:
                pkg.manual_priority = value


class SetArchOverride(Command):
    """ Sets per package build options """

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Package name or group name if --group is specified")
        parser.add_argument('arch_override',
                            help="arch_override passed as build option "
                                 "to koji when package is built")
        parser.add_argument('--group', action='store_true',
                            help="Apply on entire group instead of single package")

    def execute(self, db, name, arch_override, group):
        if group:
            group_obj = db.query(PackageGroup)\
                                  .filter_by(name=name).first()
            if not group_obj:
                sys.exit("Group {} not found".format(name))
            for pkg in group_obj.packages:
                pkg.arch_override = arch_override
        else:
            pkg = db.query(Package).filter_by(name=name).first()
            if not pkg:
                sys.exit("Package {} not found".format(name))
            pkg.arch_override = arch_override


class GroupCommandParser(object):
    def setup_parser(self, parser):
        parser.add_argument('--content-from-file',
                            help="Sets the list of packages in group to "
                            "contents of given file. Removes already existing if "
                            "--apend not given. Use - for standard input.")
        parser.add_argument('--append',
                            action='store_true',
                            help="Appends to existing list of packages instead "
                            "of overwriting it")

    def set_group_content(self, db, group, content_from_file, append):

        def from_fo(fo):
            content = filter(None, fo.read().split())
            if not content:
                sys.exit("Group content empty")
            data.set_group_content(db, group, content, append)

        if content_from_file == '-':
            from_fo(sys.stdin)
        elif content_from_file:
            with open(content_from_file) as fo:
                from_fo(fo)


class CreateGroup(GroupCommandParser, Command):
    """ Creates new package group """

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="New group name. In format namespace/name, or "
                            "just name for global groups")
        super(CreateGroup, self).setup_parser(parser)

    def execute(self, db, name, content_from_file, append):
        ns, name = PackageGroup.parse_name(name)
        group = db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if group:
            sys.exit("Group already exists")
        group = PackageGroup(name=name, namespace=ns)
        db.add(group)
        db.flush()
        self.set_group_content(db, group, content_from_file, append)


class EditGroup(GroupCommandParser, Command):
    """ Sets package group attributes """

    def setup_parser(self, parser):
        parser.add_argument('current_name',
                            help="Current group full name - ns/name")
        parser.add_argument('--new-name',
                            help="New group name")
        parser.add_argument('--new-namespace',
                            help="New group namespace")
        parser.add_argument('--make-global',
                            action='store_true',
                            help="Sets group as global (unsets namespace)")
        super(EditGroup, self).setup_parser(parser)

    def execute(self, db, current_name, new_name, new_namespace,
                make_global, content_from_file, append):
        ns, name = PackageGroup.parse_name(current_name)
        group = db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if not group:
            sys.exit("Group {} not found".format(current_name))
        if new_name:
            group.name = new_name
        if new_namespace:
            group.namespace = new_namespace
        if make_global:
            group.namespace = None
        self.set_group_content(db, group, content_from_file, append)


class EntityCommand(object):
    # entity needs to be overriden
    def get(self, db, name, **kwargs):
        return db.query(self.entity).filter(self.entity.name == name).first()


class CreateEntityCommand(EntityCommand):
    def execute(self, db, **kwargs):
        instance = self.get(db, **kwargs)
        if instance:
            sys.exit("Object already exists")
        instance = self.entity(**kwargs)
        db.add(instance)
        return instance


class EditEntityCommand(EntityCommand):
    def execute(self, db, **kwargs):
        instance = self.get(db, **kwargs)
        if not instance:
            sys.exit("Object not found")
        for key, value in kwargs.items():
            if value is not None:
                setattr(instance, key, value)
        return instance


class DeleteEntityCommand(EntityCommand):
    def execute(self, db, **kwargs):
        instance = self.get(db, **kwargs)
        if not instance:
            sys.exit("Object not found")
        db.delete(instance)


class CollectionModeAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values == 'secondary' if values else None)


class CreateOrEditCollectionCommand(object):
    args_required = True
    needs_koji = True

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-d', '--display-name',
                            required=self.args_required,
                            help="Human readable name")
        parser.add_argument('-t', '--target',
                            required=self.args_required,
                            help="Koji target")
        parser.add_argument('-m', '--mode', choices=('primary', 'secondary'),
                            dest='secondary_mode', action=CollectionModeAction,
                            help="Whether target should be in secondary "
                            "or primary mode (default)")
        parser.add_argument('-p', '--priority-coefficient',
                            help="Priority coefficient")
        parser.add_argument('-o', '--order',
                            help="Order when displaying. "
                            "First collection becomes the default")
        parser.add_argument('--build-group',
                            help="Build group name")
        parser.add_argument('--branch',
                            help="Git branch name. Used by PkgDB plugin. Optional")
        parser.add_argument('--poll-untracked',
                            help="Whether to poll builds for untracked packages. "
                            "Defaults to true")
        parser.add_argument('--bugzilla-product',
                            help="Product used in bugzilla template and links")
        parser.add_argument('--bugzilla-version',
                            help="Product version used in bugzilla template")


    def set_koji_tags(self, koji_sessions, collection):
        if collection.secondary_mode:
            koji_session = koji_sessions['secondary']
        else:
            koji_session = koji_sessions['primary']
        target_info = koji_session.getBuildTarget(collection.target)
        if not target_info:
            sys.exit("Target not found in Koji")
        collection.dest_tag = target_info['dest_tag_name']
        collection.build_tag = target_info['build_tag_name']


class CreateCollection(CreateOrEditCollectionCommand, CreateEntityCommand, Command):
    """ Creates new package collection """
    entity = Collection

    def execute(self, db, koji_sessions, **kwargs):
        collection = super(CreateCollection, self).execute(db, **kwargs)
        self.set_koji_tags(koji_sessions, collection)


class EditCollection(CreateOrEditCollectionCommand, EditEntityCommand, Command):
    """ Modifies existing package collection """
    entity = Collection
    args_required = False

    def execute(self, db, koji_sessions, **kwargs):
        collection = super(EditCollection, self).execute(db, **kwargs)
        if kwargs['secondary_mode'] is not None or kwargs['target'] is not None:
            self.set_koji_tags(koji_sessions, collection)


class DeleteCollection(Command):
    """ Deletes collection, including packages and builds it constains.
    May take long time to execute. """

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-f', '--force', action='store_true')

    def execute(self, db, name, force):
        collection = db.query(Collection).filter_by(name=name).first()
        if not collection:
            sys.exit("Collection not found")
        if (not force and
                db.query(Package).filter_by(collection_id=collection.id).count()):
            sys.exit("The collection contains packages. Specify --force to delete "
                     "it anyway. It means deleting all the packages build history. "
                     "It cannot be reverted and may take long time to execute")
        db.delete(collection)


class CreateOrEditCollectionGroupCommand(object):
    args_required = True

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-d', '--display-name',
                            required=self.args_required,
                            help="Human readable name")
        parser.add_argument('-c', '--contents', nargs='*',
                            help="Signifies that remaining arguments are "
                            "group contents")

    def execute(self, db, contents, **kwargs):
        group = super(CreateOrEditCollectionGroupCommand, self).execute(db, **kwargs)
        if contents:
            data.set_collection_group_content(db, group, contents)


class CreateCollectionGroup(CreateOrEditCollectionGroupCommand,
                            CreateEntityCommand, Command):
    """ Creates new package collection group """
    entity = CollectionGroup


class EditCollectionGroup(CreateOrEditCollectionGroupCommand,
                          EditEntityCommand, Command):
    """ Modifies existing package collection group """
    args_required = False
    entity = CollectionGroup



class DeleteCollectionGroup(DeleteEntityCommand, Command):
    """ Deletes collection group """
    entity = CollectionGroup

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")


class Psql(Command):
    """ Convenience to get psql shell connected to koschei DB. """

    needs_backend = False
    needs_db = False

    def setup_parser(self, parser):
        parser.add_argument('args', nargs='*',
                            help="Arguments passed to psql")

    def execute(self, args):
        engine = get_engine()
        cmd = ['psql', '-d', engine.url.database] + args
        if engine.url.username:
            cmd += ['-U', engine.url.username]
        if engine.url.host:
            cmd += ['-h', engine.url.host]
        env = os.environ.copy()
        if engine.url.password:
            env['PGPASSWORD'] = engine.url.password
        os.execve('/usr/bin/psql', cmd, env)


class SubmitBuild(Command):
    """ Forces scratch-build for given packages to be submitted to Koji. """

    needs_koji_login = True

    def setup_parser(self, parser):
        # TODO allow selecting particular collection
        parser.add_argument('names', nargs='+')

    def execute(self, db, koji_sessions, names):
        pkgs = db.query(Package)\
            .filter(Package.name.in_(names)).all()
        backend = Backend(db=db, koji_sessions=koji_sessions)
        for pkg in pkgs:
            backend.submit_build(pkg)


if __name__ == '__main__':
    main()
