from models import PriorityChange, Dependency, current_priorities
from plugins import plugin

@plugin('repo_done')
def apply_depchange_priority(db_session):
    current_priorities(db_session).filter_by(plugin_name='dependency')\
                                  .update({'effective': True})
    db_session.commit()

def add_dependency_change(db_session, dependency, package, new_priority):
    change = PriorityChange(package_id=package.id, effective=False,
                            plugin_name='dependency', value=new_priority,
                            comment='Dependency {} updated'\
                                    .format(dependency.name))
    db_session.add(change)
    db_session.commit()

@plugin('build_tagged')
def package_updated(db_session, package):
    visited = set()
    def recursive_update(pkgs, level=1):
        new_priority = 30 // level # TODO Bulgarian constant
        if new_priority:
            pkg_ids = [pkg.id for pkg in pkgs]
            visited.update(pkg_ids)
            deps = db_session.query(Dependency)\
                   .filter(Dependency.dependency_id.in_(pkg_ids)).all()
            pkgs_on_level = [dep.package for dep in deps if dep.package_id
                             not in visited]
            if pkgs_on_level:
                for pkg in pkgs_on_level:
                    if pkg.watched:
                        add_dependency_change(db_session, package, pkg, new_priority)
                recursive_update(pkgs_on_level, level + 1)

    recursive_update({package})
