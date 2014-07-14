#!/usr/bin/python
from __future__ import print_function

import sys
import argparse

from koschei.models import engine, Session, Base, Package, PackageGroup, \
                           PackageGroupRelation
from koschei import util

def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)

class Command(object):
    needs_db = True
    needs_koji = False

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
    db_session = None
    if cmd.needs_db:
        db_session = Session()
        kwargs['db_session'] = db_session
    if cmd.needs_koji:
        kwargs['koji_session'] = util.create_koji_session(anonymous=True)
    cmd.execute(**kwargs)
    if db_session:
        db_session.commit()
        db_session.close()

class CreateDb(Command):
    """ Creates database tables """

    needs_db = False

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

    def execute(self, db_session, names, group, static_priority, manual_priority):
        existing = [x for [x] in db_session.query(Package.name)\
                                 .filter(Package.name.in_(names))]
        if existing:
            fail("Packages already exist: " + ','.join(existing))
        if group:
            group_obj = db_session.query(PackageGroup).filter_by(name=group).first()
            if not group_obj:
                group_obj = PackageGroup(name=group)
                db_session.add(group_obj)
                db_session.flush()
        pkgs = []
        for name in names:
            pkg = Package(name=name)
            pkg.static_priority = static_priority or 0
            pkg.manual_priority = manual_priority or 30
            db_session.add(pkg)
            pkgs.append(pkg)
        db_session.flush()
        if group:
            for pkg in pkgs:
                rel = PackageGroupRelation(group_id=group_obj.id, package_id=pkg.id)
                db_session.add(rel)
        db_session.commit()


class SetPrio(Command):
    """ Sets packages priority to given value """

    def setup_parser(self, parser):
        parser.add_argument('name')
        parser.add_argument('value')
        parser.add_argument('--static', action='store_true')

    def execute(self, db_session, name, value, static):
        pkg = db_session.query(Package).filter_by(name=name).first()
        if not pkg:
            fail("Package {} not found".format(name))
        if static:
            pkg.static_priority = value
        else:
            pkg.manual_priority = value

if __name__ == '__main__':
    main()
