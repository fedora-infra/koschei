#!/usr/bin/python
import sys
import md5

from koschei import plugins, dep_loader
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
        pkg = s.query(Package).filter_by(name=sys.argv[2]).first()
        if not pkg:
            name = sys.argv[2]
            pkg = Package(name=name, watched=True, static_priority=prio,
                          manual_priority=int(md5.md5(name).hexdigest(), 16) % 30)
            s.add(pkg)
        else:
            pkg.watched = True
            pkg.static_priority = prio
        s.commit()
        plugins.dispatch_event('add_package', s, pkg)
    elif cmd == 'adddep':
        pkgname = sys.argv[2]
        depname = sys.argv[3]
        pkg = s.query(Package).filter_by(name=pkgname).one()
        dep = s.query(Package).filter_by(name=depname).first()
        if not dep:
            dep = Package(name=depname, watched=False)
            s.add(dep)
            s.commit()
        assoc = Dependency(package_id=pkg.id, dependency_id=dep.id)
        s.add(assoc)
        s.commit()
    else:
        print('No such command')
        sys.exit(1)

    s.close()
