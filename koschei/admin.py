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

# pylint: disable=arguments-differ
import re
import os
import sys
import pwd
import logging
import argparse

from koschei import data, backend, plugin
from koschei.backend import koji_util
from koschei.db import get_engine, create_all, get_or_create
from koschei.models import (
    Package, PackageGroup, AdminNotice, Collection, User, LogEntry,
    CollectionGroup, CollectionGroupRelation,
)
from koschei.config import get_config


class KoscheiAdminSession(backend.KoscheiBackendSession):
    def __init__(self, log):
        super(KoscheiAdminSession, self).__init__()
        self.log = log

    def log_user_action(self, message, **kwargs):
        username = os.environ.get('SUDO_USER', pwd.getpwuid(os.getuid()).pw_name)
        user = get_or_create(self.db, User, name=username)
        self.db.add(LogEntry(environment='admin', user=user, message=message, **kwargs))
        print(message)


class Command(object):
    needs_session = True
    load_plugins = False

    def setup_parser(self, parser):
        pass

    def execute(self, **kwargs):
        raise NotImplementedError()


def main(args, session=None):
    main_parser = argparse.ArgumentParser()
    subparser = main_parser.add_subparsers()
    for Cmd in Command.__subclasses__():
        cmd_name = re.sub(r'([A-Z])', lambda s: '-' + s.group(0).lower(),
                          Cmd.__name__)[1:]
        cmd = Cmd()
        parser = subparser.add_parser(cmd_name, help=cmd.__doc__)
        cmd.setup_parser(parser)
        parser.set_defaults(cmd=cmd)
        parser.description = cmd.__doc__
    args = main_parser.parse_args(args)
    cmd = args.cmd
    kwargs = vars(args)
    log = logging.getLogger('koschei.admin')
    if not session:
        session = KoscheiAdminSession(log)
    del kwargs['cmd']
    if cmd.load_plugins:
        plugin.load_plugins('backend')
    if cmd.needs_session:
        kwargs['session'] = session
    cmd.execute(**kwargs)
    if cmd.needs_session:
        session.db.commit()


class CreateDb(Command):
    """ Creates database tables """

    needs_session = False

    def execute(self):
        from alembic.config import Config
        from alembic import command
        create_all()
        alembic_cfg = Config(get_config('alembic.alembic_ini'))
        command.stamp(alembic_cfg, "head")


class Alembic(Command):
    """ Runs arbitrary alembic command. """

    needs_session = False

    def setup_parser(self, parser):
        parser.add_argument('args', nargs=argparse.REMAINDER,
                            help="Arguments passed to alembic")

    def execute(self, args):
        from alembic.config import main as alembic_main
        config_path = get_config('alembic.alembic_ini')
        argv = ['-c', config_path] + args
        alembic_main(prog='koschei-admin alembic', argv=argv)


class Cleanup(Command):
    """ Cleans old builds and resolution changes from the database """

    load_plugins = True

    def setup_parser(self, parser):
        parser.add_argument('--older-than', type=int,
                            help="Delete builds and resolution changes "
                            "older than N months",
                            default=6)

    def execute(self, session, older_than):
        if older_than < 2:
            sys.exit("Minimal allowed value is 2 months")
        build_res = session.db.execute("""
            DELETE FROM build WHERE started < now() - '{months} month'::interval
                AND id NOT IN (
                    SELECT last_build_id AS id FROM package
                        WHERE last_build_id IS NOT null
                    UNION
                    SELECT last_complete_build_id FROM package
                        WHERE last_complete_build_id IS NOT null
                )
        """.format(months=older_than))
        resolution_res = session.db.execute("""
            DELETE FROM resolution_change
                WHERE "timestamp" < now() - '{months} month':: interval
        """.format(months=older_than))
        session.log_user_action(
            "Cleanup: Deleted {} builds".format(build_res.rowcount)
        )
        session.log_user_action(
            "Cleanup: Deleted {} resolution changes".format(resolution_res.rowcount)
        )
        plugin.dispatch_event('cleanup', session, older_than)


class SetNotice(Command):
    """ Set admin notice displayed in web interface """

    def setup_parser(self, parser):
        parser.add_argument('content')

    def execute(self, session, content):
        key = 'global_notice'
        content = content.strip()
        notice = session.db.query(AdminNotice).filter_by(key=key).first()
        notice = notice or AdminNotice(key=key)
        notice.content = content
        session.db.add(notice)
        session.log_user_action("Admin notice added: {}".format(content))


class ClearNotice(Command):
    """ Clears current admin notice """

    def execute(self, session):
        key = 'global_notice'
        session.db.query(AdminNotice).filter_by(key=key).delete()
        session.log_user_action("Admin notice cleared")


class AddPkg(Command):
    """ Adds given packages to database """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('-c', '--collection', required=True)

    def execute(self, session, names, collection):
        if collection:
            collection = session.db.query(Collection)\
                .filter_by(name=collection)\
                .first()
            if not collection:
                sys.exit("Collection not found")
        try:
            data.track_packages(session, collection, names)
        except data.PackagesDontExist as e:
            sys.exit(str(e))


class AddGroup(Command):

    def setup_parser(self, parser):
        parser.add_argument('group')
        parser.add_argument('pkgs', nargs='*')
        parser.add_argument('-m', '--maintainer', nargs='*')

    def execute(self, session, group, pkgs, maintainer):
        namespace, name = PackageGroup.parse_name(group)
        if (session.db.query(PackageGroup)
                .filter_by(namespace=namespace, name=name)
                .count()):
            sys.exit("Group already exists")
        group_obj = PackageGroup(name=name, namespace=namespace)
        session.db.add(group_obj)
        session.db.flush()
        session.log_user_action("Group {} created".format(group.full_name))
        try:
            data.set_group_content(session, group, pkgs)
            data.set_group_maintainers(session, group, maintainer)
        except data.PackagesDontExist as e:
            sys.exit(str(e))


class SetPriority(Command):
    """ Sets package's priority to given value """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('value', type=int)
        parser.add_argument('--static', action='store_true')
        parser.add_argument('--collection', required=True)

    def execute(self, session, names, value, static, collection):
        collection = session.db.query(Collection).filter_by(name=collection).first()
        if not collection:
            sys.exit("Collection not found")
        pkgs = (
            session.db.query(Package)
            .filter(Package.name.in_(names))
            .filter(Package.collection == collection)
            .all()
        )
        if len(names) != len(pkgs):
            not_found = set(names).difference(pkg.name for pkg in pkgs)
            sys.exit('Packages not found: {}'.format(','.join(not_found)))
        for pkg in pkgs:
            data.set_package_attribute(
                session, pkg,
                'static_priority' if static else 'manual_priority',
                value,
            )


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

    def execute(self, session, name, arch_override, group):
        arch_override = arch_override or None
        if group:
            group_obj = session.db.query(PackageGroup)\
                .filter_by(name=name).first()
            if not group_obj:
                sys.exit("Group {} not found".format(name))
            for pkg in group_obj.packages:
                data.set_package_attribute(session, pkg, 'arch_override', arch_override)
        else:
            pkg = session.db.query(Package).filter_by(name=name).first()
            if not pkg:
                sys.exit("Package {} not found".format(name))
            data.set_package_attribute(session, pkg, 'arch_override', arch_override)


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

    def set_group_content(self, session, group, content_from_file, append):

        def from_fo(fo):
            content = [x for x in fo.read().split() if x]
            if not content:
                sys.exit("Group content empty")
            data.set_group_content(session, group, content, append)

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

    def execute(self, session, name, content_from_file, append):
        ns, name = PackageGroup.parse_name(name)
        group = session.db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if group:
            sys.exit("Group already exists")
        group = PackageGroup(name=name, namespace=ns)
        session.db.add(group)
        session.db.flush()
        session.log_user_action("Group {} created".format(group.full_name))
        self.set_group_content(session, group, content_from_file, append)


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

    def execute(self, session, current_name, new_name, new_namespace,
                make_global, content_from_file, append):
        ns, name = PackageGroup.parse_name(current_name)
        group = session.db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if not group:
            sys.exit("Group {} not found".format(current_name))
        if new_name:
            group.name = new_name
        if new_namespace:
            group.namespace = new_namespace
        if make_global:
            group.namespace = None
        self.set_group_content(session, group, content_from_file, append)


class EntityCommand(object):
    # entity needs to be overriden
    def get(self, session, name, **kwargs):
        # pylint:disable=no-member
        return session.db.query(self.entity).filter(self.entity.name == name).first()


class CreateEntityCommand(EntityCommand):
    def execute(self, session, **kwargs):
        instance = self.get(session, **kwargs)
        if instance:
            sys.exit("Object already exists")
        # pylint:disable=no-member
        instance = self.entity(**kwargs)
        session.db.add(instance)
        return instance


class EditEntityCommand(EntityCommand):
    def execute(self, session, **kwargs):
        instance = self.get(session, **kwargs)
        if not instance:
            sys.exit("Object not found")
        for key, value in kwargs.items():
            if value is not None:
                setattr(instance, key, value)
        return instance


class DeleteEntityCommand(EntityCommand):
    def execute(self, session, **kwargs):
        instance = self.get(session, **kwargs)
        if not instance:
            sys.exit("Object not found")
        session.db.delete(instance)


class CollectionModeAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values == 'secondary' if values else None)


class CreateOrEditCollectionCommand(object):
    create = True

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        if not self.create:
            parser.add_argument('--new-name',
                                help="New name identificator")
        parser.add_argument('-d', '--display-name',
                            required=self.create,
                            help="Human readable name")
        parser.add_argument('-t', '--target',
                            required=self.create,
                            help="Koji target")
        parser.add_argument('--dest-tag',
                            help="Koji destination tag used to fetch SRPMs and builds")
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
        parser.add_argument('--poll-untracked',
                            help="Whether to poll builds for untracked packages. "
                            "Defaults to true")
        parser.add_argument('--bugzilla-product',
                            help="Product used in bugzilla template and links")
        parser.add_argument('--bugzilla-version',
                            help="Product version used in bugzilla template")

    def set_koji_tags(self, session, collection):
        if collection.secondary_mode:
            koji_session = session.koji('secondary')
        else:
            koji_session = session.koji('primary')
        target_info = koji_session.getBuildTarget(collection.target)
        if not target_info:
            sys.exit("Target not found in Koji")
        collection.build_tag = target_info['build_tag_name']
        # dest tag should by default be the same as build_tag
        if not collection.dest_tag:
            collection.dest_tag = collection.build_tag


class CreateCollection(CreateOrEditCollectionCommand, CreateEntityCommand, Command):
    """ Creates new package collection """
    entity = Collection

    def execute(self, session, **kwargs):
        collection = super(CreateCollection, self).execute(session, **kwargs)
        self.set_koji_tags(session, collection)
        session.log_user_action("Collection {} created".format(collection.name))


class EditCollection(CreateOrEditCollectionCommand, EditEntityCommand, Command):
    """ Modifies existing package collection """
    entity = Collection
    create = False

    def execute(self, session, new_name, **kwargs):
        collection = super(EditCollection, self).execute(session, **kwargs)
        if new_name:
            collection.name = new_name
        if kwargs['secondary_mode'] is not None or kwargs['target'] is not None:
            self.set_koji_tags(session, collection)
        session.log_user_action("Collection {} modified".format(collection.name))


class DeleteCollection(Command):
    """ Deletes collection, including packages and builds it constains.
    May take long time to execute. """

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-f', '--force', action='store_true')

    def execute(self, session, name, force):
        collection = session.db.query(Collection).filter_by(name=name).first()
        if not collection:
            sys.exit("Collection not found")
        if (not force and
                session.db.query(Package).filter_by(collection_id=collection.id).count()):
            sys.exit("The collection contains packages. Specify --force to delete "
                     "it anyway. It means deleting all the packages build history. "
                     "It cannot be reverted and may take long time to execute")
        session.log_user_action("Collection {} deleted".format(collection.name))
        session.db.delete(collection)


class BranchCollection(CreateOrEditCollectionCommand, Command):
    """
    Performs branching for given collection. It performs these steps:
    1. creates a branched collection with the same attributes as the master one,
       except for display name and bugzilla version passed as arguments
    2. copies most recent (1 month) builds from master collection to the branched one
    3. sets new name for the master collection and moves it to give new koji target
    """

    def setup_parser(self, parser):
        parser.add_argument(
            'master_collection',
            help="Master collection from which new collection should be branched",
        )
        parser.add_argument(
            'new_name',
            help="New name for the master collection after the branched copy is created",
        )
        parser.add_argument(
            '-d', '--display-name',
            required=True,
            help="Human readable name for branched collection",
        )
        parser.add_argument(
            '-t', '--target',
            required=True,
            help="New Koji target for the master collection",
        )
        parser.add_argument(
            '--dest-tag',
            help="New destination tag for the master collection (defaults to build tag)",
        )
        parser.add_argument(
            '--bugzilla-version',
            help="Product version used in bugzilla template for the branched collection",
        )

    def execute(self, session, master_collection, new_name, display_name,
                target, dest_tag, bugzilla_version):
        master = (
            session.db.query(Collection)
            .filter_by(name=master_collection)
        ).first()
        if not master:
            sys.exit("Collection not found")
        branched = (
            session.db.query(Collection)
            .filter_by(name=new_name)
        ).first()
        if branched:
            sys.exit("Branched collection exists already")
        branched = Collection(
            display_name=display_name,
            bugzilla_version=bugzilla_version,
        )
        for key in ('secondary_mode', 'priority_coefficient', 'bugzilla_product',
                    'poll_untracked', 'build_group', 'target', 'name', 'order',
                    'dest_tag'):
            setattr(branched, key, getattr(master, key))
        master.name = new_name
        master.target = target
        master.dest_tag = dest_tag
        master.order += 1
        self.set_koji_tags(session, branched)
        self.set_koji_tags(session, master)
        session.db.add(branched)
        session.db.flush()
        for group_rel in (
                session.db.query(CollectionGroupRelation)
                .filter_by(collection_id=master.id)
        ):
            session.db.add(CollectionGroupRelation(
                collection_id=branched.id,
                group_id=group_rel.group_id,
            ))

        data.copy_collection(session, master, branched)
        session.log_user_action(
            "Collection {} branched from {}".format(new_name, master_collection)
        )


class CreateOrEditCollectionGroupCommand(object):
    create = True

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-d', '--display-name',
                            required=self.create,
                            help="Human readable name")
        parser.add_argument('-c', '--contents', nargs='*',
                            help="Signifies that remaining arguments are "
                            "group contents")

    def execute(self, session, contents, **kwargs):
        group = super(CreateOrEditCollectionGroupCommand, self)\
            .execute(session, **kwargs)
        if contents:
            data.set_collection_group_content(session, group, contents)


class CreateCollectionGroup(CreateOrEditCollectionGroupCommand,
                            CreateEntityCommand, Command):
    """ Creates new package collection group """
    entity = CollectionGroup


class EditCollectionGroup(CreateOrEditCollectionGroupCommand,
                          EditEntityCommand, Command):
    """ Modifies existing package collection group """
    create = False
    entity = CollectionGroup


class DeleteCollectionGroup(DeleteEntityCommand, Command):
    """ Deletes collection group """
    entity = CollectionGroup

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")


class Psql(Command):
    """ Convenience to get psql shell connected to koschei DB. """
    needs_session = False

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

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('-c', '--collection')

    def execute(self, session, names, collection):
        pkgs = session.db.query(Package).join(Collection)\
            .filter(Package.name.in_(names))\
            .filter(Collection.latest_repo_resolved)
        if collection:
            pkgs = pkgs.filter(Collection.name == collection)
        for pkg in pkgs.all():
            # FIXME there is code duplication with backend/services/scheduler.py
            koji_session = session.koji('primary')
            all_arches = koji_util.get_koji_arches_cached(
                session,
                koji_session,
                pkg.collection.build_tag,
            )
            arches = koji_util.get_srpm_arches(
                koji_session=session.koji('secondary'),
                all_arches=all_arches,
                nvra=pkg.srpm_nvra,
                arch_override=pkg.arch_override,
            )
            if arches is None:
                print("No SRPM found for package {} in collection {}"
                      .format(pkg.name, pkg.collection.name))
                continue
            if not arches:
                print("No allowed arches found for package {} in collection {}"
                      .format(pkg.name, pkg.collection.name))
                continue
            backend.submit_build(
                session,
                pkg,
                arch_override=None if 'noarch' in arches else arches,
            )
