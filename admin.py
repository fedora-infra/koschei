#!/usr/bin/python
import sys

from koschei.models import *
from koschei import util

if __name__ == '__main__':
    cmd = sys.argv[1]
    s = Session()
    if cmd == 'createdb':
        Base.metadata.create_all(engine)
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config(util.config['alembic']['alembic_ini'])
        command.stamp(alembic_cfg, "head")
    elif cmd == 'dropdb':
        Base.metadata.drop_all(engine)
    elif cmd == 'addpkg':
        prio = 0
        if len(sys.argv) > 3:
            prio = int(sys.argv[3])
        name = sys.argv[2]
        pkg = Package(name=name, static_priority=prio,
                      manual_priority=30)
        s.add(pkg)
        s.commit()
    elif cmd == 'addpkgs':
        pkgs = []
        x = 100
        for name in sys.argv[2:]:
            pkg = Package(name=name, manual_priority=x // 3)
            s.add(pkg)
            s.commit()
            pkgs.append(pkg)
            x -= 1
    elif cmd == 'setprio':
        #TODO use argparse
        if sys.argv[2] == '--static':
            pkg = s.query(Package).filter_by(name=sys.argv[3]).one()
            pkg.static_priority = int(sys.argv[4])
        else:
            pkg = s.query(Package).filter_by(name=sys.argv[2]).one()
            pkg.manual_priority = int(sys.argv[3])
        s.commit()
    else:
        print('No such command')
        sys.exit(1)

    s.close()
