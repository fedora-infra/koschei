#!/usr/bin/python
import sys

from koschei import plugin
from koschei.models import *

if __name__ == '__main__':
    cmd = sys.argv[1]
    s = Session()
    if cmd == 'createdb':
        Base.metadata.create_all(engine)
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
        x = 100
        for name in sys.argv[2:]:
            pkg = Package(name=name, manual_priority=x // 3)
            s.add(pkg)
            s.commit()
            x -= 1
    else:
        print('No such command')
        sys.exit(1)

    s.close()
