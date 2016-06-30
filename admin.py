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
import logging

from koschei.models import (get_engine, Base, Package, PackageGroup, Session,
                            AdminNotice, Collection)
from koschei.backend import Backend, PackagesDontExist, koji_util
from koschei.config import load_config, get_config


load_config(['/usr/share/koschei/config.cfg', '/etc/koschei/config-admin.cfg'])


def parse_group_name(name):
    if '/' not in name:
        return None, name
    ns, _, name = name.partition('/')
    return ns, name


class Command(object):
    needs_koji_login = False
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
        primary = koji_util.KojiSession(anonymous=not cmd.needs_koji_login)
        secondary = koji_util.KojiSession(anonymous=True, koji_id='secondary')
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
            conn.execute("REINDEX DATABASE {}".format(get_engine().url.database))
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
            collection = backend.db.query(Collection)\
                .filter_by(name=collection)\
                .first()
            if not collection:
                sys.exit("Collection not found")
        try:
            backend.add_packages(names, collection_id=collection.id if
                                 collection else None)
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

    def set_group_content(self, backend, group, content_from_file, append):

        def from_fo(fo):
            content = filter(None, fo.read().split())
            if not content:
                sys.exit("Group content empty")
            backend.set_group_content(group, content, append)

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

    def execute(self, backend, name, content_from_file, append):
        ns, name = parse_group_name(name)
        group = backend.db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if group:
            sys.exit("Group already exists")
        group = PackageGroup(name=name, namespace=ns)
        backend.db.add(group)
        backend.db.flush()
        self.set_group_content(backend, group, content_from_file, append)
        backend.db.commit()


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

    def execute(self, backend, current_name, new_name, new_namespace,
                make_global, content_from_file, append):
        ns, name = parse_group_name(current_name)
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
        self.set_group_content(backend, group, content_from_file, append)
        backend.db.commit()


class DeleteGroup(Command):
    """ Deletes package group """

    def setup_parser(self, parser):
        parser.add_argument('current_name',
                            help="Current group full name - ns/name")

    def execute(self, backend, current_name):
        ns, name = parse_group_name(current_name)
        group = backend.db.query(PackageGroup)\
            .filter_by(name=name, namespace=ns or None).first()
        if not group:
            sys.exit("Group {} not found".format(current_name))
        backend.db.delete(group)
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

    def execute(self, backend, names):
        pkgs = backend.db.query(Package)\
                         .filter(Package.name.in_(names)).all()
        for pkg in pkgs:
            backend.submit_build(pkg)
        backend.db.commit()


if __name__ == '__main__':
    main()
