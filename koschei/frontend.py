from datetime import datetime
from flask import Flask, abort, render_template
from sqlalchemy.orm import scoped_session, sessionmaker, joinedload, \
                           subqueryload, undefer

from .models import engine, Package, Build, PackageGroup
from . import util

dirs = util.config['directories']
app = Flask('koschei', template_folder=dirs['templates'],
            static_folder=dirs['static'])
app.config.from_object(util.config['flask'])

db_session = scoped_session(sessionmaker(autocommit=False, bind=engine))

# Following will make pylint shut up about missing query method
if False:
    db_session.query = lambda *args: None

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

@app.template_filter('date')
def date_filter(date):
    return date.strftime("%F %T") if date else ''

@app.context_processor
def inject_defaults():
    return {'since': datetime.min, 'until': datetime.now(),
            'koji_weburl': util.config['koji_config']['weburl']}

@app.route('/static/<path:path>')
def serve_static(path):
    import os
    return app.send_static_file(os.path.join('static', path))

@app.route('/')
@app.route('/index.html')
def frontpage():
    packages = db_session.query(Package)\
                         .options(joinedload(Package.last_build))\
                         .order_by(Package.name).all()
    return render_template("frontpage.html", packages=packages)

@app.route('/package/<name>.html')
def package_detail(name):
    package = db_session.query(Package).filter_by(name=name)\
                        .options(subqueryload(Package.all_builds),
                                 subqueryload(Package.all_builds,
                                              Build.dependency_changes))\
                        .first()
    if not package:
        abort(404)
    return render_template("package-detail.html", package=package)

@app.route('/package/<name>/<int:build_id>.html')
def build_detail(name, build_id):
    #pylint: disable=E1101
    build = db_session.query(Build)\
            .options(joinedload(Build.package),
                     subqueryload(Build.buildroot_diff))\
            .filter_by(id=build_id).first()
    if not build or build.package.name != name:
        abort(404)
    return render_template("build-detail.html", build=build)

@app.route('/groups.html')
def groups_overview():
    groups = db_session.query(PackageGroup)\
                       .options(undefer(PackageGroup.package_count))\
                       .order_by(PackageGroup.name).all()
    return render_template("groups.html", groups=groups)

@app.route('/group/<int:group_id>.html')
def group_detail(group_id):
    group = db_session.query(PackageGroup)\
                      .options(subqueryload(PackageGroup.packages),
                               joinedload(PackageGroup.packages,
                                          Package.last_build))\
                      .filter_by(id=group_id).first()
    if not group:
        abort(404)
    return render_template("group-detail.html", group=group)

if __name__ == '__main__':
    app.run()
