import subprocess

from models import config, Package, Dependency, Session

def refresh_repo():
    session = Session()
    try:
        packages = session.query(Package)
        for pkg in packages:
            process_package(session, pkg)
    finally:
        session.close()

def get_repoquery_invocation():
    repos = config['repos']
    base_invocation = ['repoquery']
    for i, repo in enumerate(repos):
        repoid = 'ci-repo-{}'.format(i)
        base_invocation.append('--repofrompath={},{}'.format(repoid, repo))
        base_invocation.append('--repoid={}'.format(repoid))
    return base_invocation

def process_package(session, pkg):
    invocation = get_repoquery_invocation()
    invocation += ['--requires', '--resolve', '--qf=%{name}', pkg.name]
    deps = subprocess.check_output(invocation).split('\n')
    deps = filter(None, deps)
    process_deps(session, pkg, deps)

def process_deps(session, pkg, deps):
    existing = {assoc.dependency.name: assoc for assoc in pkg.dependencies}
    keep = set()
    for dep_name in set(deps):
        if dep_name in existing:
            keep.add(existing[dep_name])
        else:
            dep_pkg = session.query(Package).filter_by(name=dep_name).first()
            if not dep_pkg:
                dep_pkg = Package(name=dep_name, watched=False)
                session.add(dep_pkg)
                session.commit()
                process_package(session, dep_pkg)
            assoc = Dependency(package_id=pkg.id, dependency_id=dep_pkg.id)
            session.add(assoc)
            session.commit()
    for dep in set(existing.values()).difference(keep):
        session.delete(dep)
        session.commit()

def add_all_packages(session):
    invocation = get_repoquery_invocation()
    invocation += ['--all', '--qf=%{name}']
    pkgs = subprocess.check_output(invocation).split('\n')
    pkgs = set(filter(None, pkgs))
    existing = {pkg.name for pkg in session.query(Package)}
    for new in pkgs.difference(existing):
        pkg = Package(name=new, watched=False)
        session.add(pkg)
        session.commit()

if __name__ == '__main__':
    refresh_repo()
