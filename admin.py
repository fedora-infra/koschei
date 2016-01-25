#!/usr/bin/python
from __future__ import print_function

# pylint: disable=W0221
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
                            AdminNotice)
from koschei.backend import Backend, PackagesDontExist
from koschei import util


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


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
        cmd_name = Cmd.__name__.lower()
        cmd = Cmd()
        parser = subparser.add_parser(cmd_name, help=cmd.__doc__)
        cmd.setup_parser(parser)
        parser.set_defaults(cmd=cmd)
    args = main_parser.parse_args()
    cmd = args.cmd
    kwargs = vars(args)
    del kwargs['cmd']
    if cmd.needs_backend:
        koji_session = util.KojiSession(anonymous=True)
        secondary_koji = util.KojiSession(anonymous=True,
                                          koji_config=util.secondary_koji_config)
        backend = Backend(db=Session(), log=logging.getLogger(),
                          koji_session=koji_session, secondary_koji=secondary_koji)
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
            fail("Minimal allowed value is 2 months")
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
        if reindex:
            conn.execute("REINDEX TABLE build")
            conn.execute("REINDEX TABLE dependency_change")
            conn.execute("REINDEX TABLE koji_task")
            print("Reindexed")
        if vacuum:
            conn.execute("VACUUM FULL ANALYZE")
            print("Vacuum full analyze done")


class Notice(Command):
    """ Set admin notice displayed in web interface """

    def setup_parser(self, parser):
        parser.add_argument('content')

    def execute(self, backend, content):
        db = backend.db
        key = 'global_notice'
        content = content.strip()
        notice = db.query(AdminNotice).filter_by(key=key).first()
        if not content or content == 'clear':
            if notice:
                db.delete(notice)
        else:
            notice = notice or AdminNotice(key=key)
            notice.content = content
            db.add(notice)


class AddPkg(Command):
    """ Adds given packages to database """

    def setup_parser(self, parser):
        parser.add_argument('names', nargs='+')
        parser.add_argument('-s', '--static-priority', type=int)
        parser.add_argument('-m', '--manual-priority', type=int)
        parser.add_argument('-g', '--group')

    def execute(self, backend, names, group, static_priority, manual_priority):
        try:
            backend.add_packages(names, group=group, static_priority=static_priority,
                                 manual_priority=manual_priority)
        except PackagesDontExist as e:
            fail("Packages don't exist: " + ','.join(e.names))


class AddGrp(Command):

    def setup_parser(self, parser):
        parser.add_argument('group')
        parser.add_argument('pkgs', nargs='*')

    def execute(self, backend, group, pkgs):
        pkg_objs = backend.db.query(Package)\
                             .filter(Package.name.in_(pkgs)).all()
        names = {pkg.name for pkg in pkg_objs}
        if names != set(pkgs):
            fail("Packages not found: " + ','.join(set(pkgs).difference(names)))
        backend.add_group(group, pkg_objs)


class SetPrio(Command):
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
            fail('Packages not found: {}'.format(','.join(not_found)))
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
                fail("Group {} not found".format(name))
            for pkg in group_obj.packages:
                pkg.arch_override = arch_override
        else:
            pkg = backend.db.query(Package).filter_by(name=name).first()
            if not pkg:
                fail("Package {} not found".format(name))
            pkg.arch_override = arch_override


class ModifyGroup(Command):
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
            fail("Group {} not found".format(current_name))
        if new_name:
            group.name = new_name
        if new_namespace:
            group.namespace = new_namespace
        if make_global:
            group.namespace = None
        backend.db.commit()


class PSQL(Command):
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
