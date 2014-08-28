import urllib

from datetime import datetime
from flask import Flask, abort, render_template, request
from flask_sqlalchemy import BaseQuery
from sqlalchemy.orm import scoped_session, sessionmaker, joinedload, \
                           subqueryload, undefer, contains_eager
from sqlalchemy.sql import literal_column

from .models import engine, Package, Build, PackageGroup, PackageGroupRelation
from . import util

dirs = util.config['directories']
app = Flask('koschei', template_folder=dirs['templates'],
            static_folder=dirs['static_folder'], static_url_path=dirs['static_url'])
app.config.from_object(util.config['flask'])

frontend_config = util.config['frontend']
items_per_page = frontend_config['items_per_page']

db_session = scoped_session(sessionmaker(autocommit=False, bind=engine,
                            query_cls=BaseQuery))

# Following will make pylint shut up about missing query method
if False:
    db_session.query = lambda *args: None

def page_args(page=None, order_by=None):
    def proc_order(order):
        new_order = []
        for item in order:
            if item not in new_order and '-' + item not in new_order:
                new_order.append(item)
        return ','.join(new_order)
    args = {
        'page': page or request.args.get('page'),
        'order_by': proc_order(order_by) if order_by else request.args.get('order_by'),
        }
    return urllib.urlencode({k: '' if v is True else v for k, v in args.items() if v})

app.jinja_env.globals.update(koji_weburl=util.config['koji_config']['weburl'],
                             min=min, max=max, page_args=page_args)

def get_order(order_map, order_spec):
    orders = []
    components = order_spec.split(',')
    for component in components:
        if component:
            if component.startswith('-'):
                order = [col.desc() for col in order_map.get(component[1:])]
            else:
                order = order_map.get(component)
            orders.extend(order)
    if any(order is None for order in orders):
        abort(400)
    return components, orders

def package_view(template, alter_query=None, **template_args):
    order_name = request.args.get('order_by', 'name')
    #pylint: disable=E1101
    order_map = {'name': [Package.name],
                 'state': [Package.state, literal_column('last_complete_build.state')],
                 'task_id': [literal_column('last_complete_build.task_id')],
                 'started': [literal_column('last_complete_build.started')],
                 }
    order_names, order = get_order(order_map, order_name)
    page_no = int(request.args.get('page', 1))
    pkgs = db_session.query(Package)\
                     .outerjoin(Package.last_complete_build)\
                     .options(contains_eager(Package.last_complete_build))\
                     .order_by(*order)
    if alter_query:
        pkgs = alter_query(pkgs)
    page = pkgs.paginate(page=page_no, per_page=items_per_page)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, **template_args)

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

@app.template_filter('date')
def date_filter(date):
    return date.strftime("%F %T") if date else ''

@app.context_processor
def inject_times():
    return {'since': datetime.min, 'until': datetime.now()}

@app.route('/')
def frontpage():
    return package_view("frontpage.html")

@app.route('/package/<name>')
def package_detail(name):
    package = db_session.query(Package).filter_by(name=name)\
                        .options(subqueryload(Package.all_builds),
                                 subqueryload(Package.all_builds,
                                              Build.dependency_changes))\
                        .first_or_404()
    return render_template("package-detail.html", package=package)

@app.route('/package/<name>/<int:build_id>')
def build_detail(name, build_id):
    #pylint: disable=E1101
    build = db_session.query(Build)\
            .options(joinedload(Build.package),
                     subqueryload(Build.dependency_changes))\
            .filter_by(id=build_id).first()
    if not build or build.package.name != name:
        abort(404)
    return render_template("build-detail.html", build=build)

@app.route('/groups')
def groups_overview():
    groups = db_session.query(PackageGroup)\
                       .options(undefer(PackageGroup.package_count))\
                       .order_by(PackageGroup.name).all()
    return render_template("groups.html", groups=groups)

@app.route('/groups/<int:id>')
@app.route('/groups/<name>')
def group_detail(name=None, id=None):
    filt = {'name': name} if name else {'id': id}
    group = db_session.query(PackageGroup)\
                      .filter_by(**filt).first_or_404()
    def alter_query(q):
        return q.outerjoin(PackageGroupRelation)\
                .filter(PackageGroupRelation.group_id == group.id)
    return package_view("group-detail.html", alter_query=alter_query,
                        group=group)

if __name__ == '__main__':
    app.run()
