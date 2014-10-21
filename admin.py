#!/usr/bin/python
from __future__ import print_function

import sys
import argparse
import json
import logging

from koschei.models import engine, Base, Package, PackageGroup, Session
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
        backend = Backend(db=Session(), log=logging.getLogger(),
                          koji_session=util.create_koji_session(anonymous=True))
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

def validate_opts(build_opts):
    try:
        decoded = json.loads(build_opts)
    except ValueError as e:
        fail(e.message)
    if not isinstance(decoded, dict):
        fail("Not a JSON object")

class SetOpts(Command):
    """ Sets per package build options """

    def setup_parser(self, parser):
        parser.add_argument('name',
                help="Package name or group name if --group is specified")
        parser.add_argument('build_opts',
                help="JSON object representing build options passed to Koji")
        parser.add_argument('--group', action='store_true',
                help="Apply on entire group instead of single package")

    def execute(self, backend, name, build_opts, group):
        validate_opts(build_opts)
        if group:
            group_obj = backend.db.query(PackageGroup)\
                                          .filter_by(name=name).first()
            if not group_obj:
                fail("Group {} not found".format(name))
            for pkg in group_obj.packages:
                pkg.build_opts = build_opts
        else:
            pkg = backend.db.query(Package).filter_by(name=name).first()
            if not pkg:
                fail("Package {} not found".format(name))
            pkg.build_opts = build_opts

if __name__ == '__main__':
    main()
