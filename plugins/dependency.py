from models import *
from plugins import plugin

@plugin('repo_done')
def apply_depchange_priority(db_session):
    changes = db_session.query(PluginData)\
               .filter_by(plugin_name='dependency', key='dep_score')
    for change in changes:
        pkg = change.package
        pkg.priority += int(change.value)
        db_session.delete(change)
        db_session.commit()

def inc_depchange(db_session, package, by=1):
    score = package.plugin_data.filter_by(plugin_name='dependency',
                                          key='dep_score').first()
    if score:
        score.value = str(int(score.value) + by)
    else:
        score = PluginData(plugin_name='dependency', key='dep_score',
                           value=str(by), package_id=package.id)
        db_session.add(score)
    db_session.commit()

@plugin('build_tagged')
def package_updated(db_session, package):
    visited = set()
    def recursive_update(pkgs, increment):
        pkg_ids = [pkg.id for pkg in pkgs]
        visited.update(pkg_ids)
        deps = db_session.query(Dependency)\
               .filter(Dependency.dependency_id.in_(pkg_ids)).all()
        pkgs_on_level = [dep.package for dep in deps if dep.package_id not in visited]
        if pkgs_on_level:
            for pkg in pkgs_on_level:
                if pkg.watched:
                    inc_depchange(db_session, pkg, by=increment)
            new_increment = increment - 15
            if new_increment > 0:
                recursive_update(pkgs_on_level, new_increment)
    recursive_update({package}, 30)
