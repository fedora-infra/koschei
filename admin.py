#!/usr/bin/python
from __future__ import print_function

# pylint: disable=W0221
import re
import os
import sys
import argparse
import logging

# FIXME we should have some nicer solution to override configs
if 'KOSCHEI_CONFIG' not in os.environ:
    os.environ['KOSCHEI_CONFIG'] = ('/usr/share/koschei/config.cfg:'
                                    '/etc/koschei/config.cfg:'
                                    '/etc/koschei/config-admin.cfg')

from koschei.models import (engine, Base, Package, PackageGroup, Session,
                            AdminNotice, Collection)
from koschei.backend import Backend, PackagesDontExist
from koschei import util


class Command(object):
    needs_backend = True

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
    if cmd.needs_backend:
        primary = util.KojiSession(anonymous=True)
        secondary = util.KojiSession(anonymous=True, koji_id='secondary')
        backend = Backend(db=Session(), log=logging.getLogger(),
                          koji_sessions={'primary': primary, 'secondary': secondary})
        kwargs['backend'] = backend
    cmd.execute(**kwargs)
    if cmd.needs_backend:
        backend.db.commit()
        backend.db.close()


class CreateDb(Command):
    """ Creates database tables """

    needs_backend = False

    def execute(self):
        from alembic.config import Config
        from alembic import command
        Base.metadata.create_all(engine)
        alembic_cfg = Config(util.config['alembic']['alembic_ini'])
        command.stamp(alembic_cfg, "head")


class Cleanup(Command):
    """ Cleans old builds from the database """

    def setup_parser(self, parser):
        parser.add_argument('--older-than', type=int,
                            help="Delete builds older than N months",
                            default=6)
        parser.add_argument('--reindex', action='store_true')
        parser.add_argument('--vacuum', action='store_true')

    def execute(self, backend, older_than, reindex, vacuum):
        if older_than < 2:
            sys.exit("Minimal allowed value is 2 months")
        conn = backend.db.connection()
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
            conn.execute("REINDEX DATABASE {}".format(engine.url.database))
            print("Reindexed")


class SetNotice(Command):
    """ Set admin notice displayed in web interface """

    def setup_parser(self, parser):
        parser.add_argument('content')

    def execute(self, backend, content):
        db = backend.db
        key = 'global_notice'
        content = content.strip()
        notice = db.query(AdminNotice).filter_by(key=key).first()
        notice = notice or AdminNotice(key=key)
        notice.content = content
        db.add(notice)


class ClearNotice(Command):
    """ Clears current admin notice """

    def execute(self, backend):
        key = 'global_notice'
        backend.db.query(AdminNotice).filter_by(key=key).delete()
        backend.db.commit()


class AddPkg(Command):
    """ Adds given packages to database """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('-c', '--collection')

    def execute(self, backend, names, collection):
        if collection:
            collection = backend.db.query(Collection).first()
            if not collection:
                sys.exit("Collection not found")
        try:
            backend.sync_tracked(names, collection_id=collection.id)
        except PackagesDontExist as e:
            sys.exit("Packages don't exist: " + ','.join(e.names))


class AddGroup(Command):

    def setup_parser(self, parser):
        parser.add_argument('group')
        parser.add_argument('pkgs', nargs='*')

    def execute(self, backend, group, pkgs):
        pkg_objs = backend.db.query(Package)\
                             .filter(Package.name.in_(pkgs)).all()
        names = {pkg.name for pkg in pkg_objs}
        if names != set(pkgs):
            sys.exit("Packages not found: " + ','.join(set(pkgs).difference(names)))
        backend.add_group(group, pkg_objs)


class SetPriority(Command):
    """ Sets package's priority to given value """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('value')
        parser.add_argument('--static', action='store_true')

    def execute(self, backend, names, value, static):
        pkgs = backend.db.query(Package)\
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

    def execute(self, backend, name, arch_override, group):
        if group:
            group_obj = backend.db.query(PackageGroup)\
                                  .filter_by(name=name).first()
            if not group_obj:
                sys.exit("Group {} not found".format(name))
            for pkg in group_obj.packages:
                pkg.arch_override = arch_override
        else:
            pkg = backend.db.query(Package).filter_by(name=name).first()
            if not pkg:
                sys.exit("Package {} not found".format(name))
            pkg.arch_override = arch_override


class EditGroup(Command):
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

    def execute(self, backend, current_name, new_name, new_namespace, make_global):
        ns, _, name = current_name.partition('/')
        if not name:
            ns, name = name, ns
        group = backend.db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if not group:
            sys.exit("Group {} not found".format(current_name))
        if new_name:
            group.name = new_name
        if new_namespace:
            group.namespace = new_namespace
        if make_global:
            group.namespace = None
        backend.db.commit()


class CollectionCommandParser(object):
    args_required = True

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-d', '--display-name',
                            required=self.args_required,
                            help="Human readable name")
        parser.add_argument('-t', '--target-tag',
                            required=self.args_required,
                            help="Koji target tag")
        parser.add_argument('-b', '--build-tag',
                            required=self.args_required,
                            help="Koji build tag")
        parser.add_argument('-p', '--priority-coefficient',
                            help="Priority coefficient")
        parser.add_argument('-o', '--order',
                            help="Order when displaying. "
                            "First collection becomes the default")
        parser.add_argument('--resolution-arches',
                            help="Comma separated list of arches which should "
                            "be included in resolution sack")
        parser.add_argument('--resolve-for-arch',
                            help="Architecture for which resolution will be done")
        parser.add_argument('--build-group',
                            help="Build group name")
        parser.add_argument('--branch',
                            help="Git branch name. Used by PkgDB plugin. Optional")


class CreateCollection(CollectionCommandParser, Command):
    """ Creates new package collection """

    def execute(self, backend, **kwargs):
        backend.db.add(Collection(**kwargs))
        backend.db.commit()


class EditCollection(CollectionCommandParser, Command):
    """ Modifies existing package collection """
    args_required = False

    def execute(self, backend, name, **kwargs):
        collection = backend.db.query(Collection).filter_by(name=name).first()
        if not collection:
            sys.exit("Collection not found")
        for key, value in kwargs.items():
            if value is not None:
                setattr(collection, key, value)
        backend.db.commit()


class DeleteCollection(Command):
    """ Deletes collection, including packages and builds it constains.
    May take long time to execute. """

    def setup_parser(self, parser):
        parser.add_argument('name',
                            help="Name identificator")
        parser.add_argument('-f', '--force', action='store_true')

    def execute(self, backend, name, force):
        collection = backend.db.query(Collection).filter_by(name=name).first()
        if not collection:
            sys.exit("Collection not found")
        if (not force and
                backend.db.query(Package).filter_by(collection_id=collection.id).count()):
            sys.exit("The collection contains packages. Specify --force to delete "
                     "it anyway. It means deleting all the packages build history. "
                     "It cannot be reverted and may take long time to execute")
        backend.db.delete(collection)
        backend.db.commit()


class Psql(Command):
    """ Convenience to get psql shell connected to koschei DB. """

    needs_backend = False

    def setup_parser(self, parser):
        parser.add_argument('args', nargs='*',
                            help="Arguments passed to psql")

    def execute(self, args):
        cmd = ['psql', '-d', engine.url.database] + args
        if engine.url.username:
            cmd += ['-U', engine.url.username]
        if engine.url.host:
            cmd += ['-h', engine.url.host]
        env = os.environ.copy()
        if engine.url.password:
            env['PGPASSWORD'] = engine.url.password
        os.execve('/usr/bin/psql', cmd, env)


if __name__ == '__main__':
    main()
