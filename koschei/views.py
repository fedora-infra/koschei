import re
import koji
import urllib
import logging

from datetime import datetime
from functools import wraps
from flask import abort, render_template, request, url_for, redirect, g, flash
from sqlalchemy.orm import joinedload, subqueryload, undefer, contains_eager
from jinja2 import Markup, escape
from textwrap import dedent

from .models import (Package, Build, PackageGroup, PackageGroupRelation,
                     AdminNotice, User, UserPackageRelation, get_or_create)
from . import util, auth, backend, main, plugin
from .frontend import app, db, frontend_config

log = logging.getLogger('koschei.views')

packages_per_page = frontend_config['packages_per_page']
builds_per_page = frontend_config['builds_per_page']

main.load_globals()

def create_backend():
    return backend.Backend(db=db, log=log,
                           koji_session=util.create_koji_session(anonymous=True))


def page_args(page=None, order_by=None):
    def proc_order(order):
        new_order = []
        for item in order:
            if (item.replace('-', '')
                    not in new_order and '-' + item not in new_order):
                new_order.append(item)
        return ','.join(new_order)
    args = {'page': page or request.args.get('page'),
            'order_by': proc_order(order_by) if order_by
                        else request.args.get('order_by')}
    return urllib.urlencode({k: '' if v is True else v for k, v in args.items()
                             if v})


def format_evr(epoch, version, release):
    if not version or not release:
        return ''
    if len(release) > 16:
        release = release[:13] + '...'
    if epoch:
        return '{}:{}-{}'.format(epoch, version, release)
    return '{}-{}'.format(version, release)


def format_depchange(change):
    if change:
        is_update = util.compare_evr(change.prev_evr, change.curr_evr) < 0
        return (change.dep_name, format_evr(*change.prev_evr),
                '<>'[is_update], format_evr(*change.curr_evr))

    return [''] * 4


def columnize(what, css_class=None):
    attrs = ' class="{}"'.format(css_class) if css_class else ''
    return Markup('\n'.join('<td{}>{}</td>'.format(attrs, escape(item))
                            for item in what))

def get_global_notice():
    return db.query(AdminNotice).filter_by(key="global_notice").first()


pathinfo = koji.PathInfo(topdir=util.koji_config['topurl'])
app.jinja_env.globals.update(koji_weburl=util.config['koji_config']['weburl'],
                             koji_pathinfo=pathinfo, next=next, iter=iter,
                             min=min, max=max, page_args=page_args,
                             get_global_notice=get_global_notice)

app.jinja_env.filters.update(columnize=columnize,
                             format_depchange=format_depchange)



class Reversed(object):
    def __init__(self, content):
        self.content = content

    def desc(self):
        return self.content

    def asc(self):
        return self.content.desc()


class PriorityOrder(Reversed):
    def asc(self):
        return self.content.desc().nullslast()


def get_order(order_map, order_spec):
    orders = []
    components = order_spec.split(',')
    for component in components:
        if component:
            if component.startswith('-'):
                order = [o.desc() for o in order_map.get(component[1:])]
            else:
                order = [o.asc() for o in order_map.get(component)]
            orders.extend(order)
    if any(order is None for order in orders):
        abort(400)
    return components, orders


def package_view(package_query, template, keep_ignored=False, **template_args):
    order_name = request.args.get('order_by', 'name')
    # pylint: disable=E1101
    order_map = {'name': [Package.name],
                 'state': [Package.resolved, Reversed(Build.state)],
                 'task_id': [Build.task_id],
                 'started': [Build.started],
                 'current_priority': [PriorityOrder(Package.current_priority)]}
    order_names, order = get_order(order_map, order_name)
    pkgs = (package_query if keep_ignored
            else package_query.filter(Package.ignored == False))\
             .outerjoin(Package.last_complete_build)\
             .options(contains_eager(Package.last_complete_build))\
             .order_by(*order)
    page = pkgs.paginate(packages_per_page)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, **template_args)


@property
def state_icon(package):
    icon = {'ok': 'complete',
            'failing': 'failed',
            'unresolved': 'cross'}.get(package.state_string, 'unknown')
    return url_for('static', filename='images/{name}.png'.format(name=icon))
Package.state_icon = state_icon

tabs = []


def tab(caption, slave=False):
    def decorator(fn):
        if not slave:
            tabs.append((fn.__name__, caption))

        @wraps(fn)
        def decorated(*args, **kwargs):
            g.tabs = tabs
            g.current_tab = fn.__name__
            return fn(*args, **kwargs)
        return decorated
    return decorator


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.remove()


@app.template_filter('date')
def date_filter(date):
    return date.strftime("%F %T") if date else ''


@app.context_processor
def inject_times():
    return {'since': datetime.min, 'until': datetime.now()}


@app.context_processor
def inject_fedmenu():
    if 'fedmenu_url' in frontend_config:
        return {
            'fedmenu_url': frontend_config['fedmenu_url'],
            'fedmenu_data_url': frontend_config['fedmenu_data_url'],
        }
    else:
        return {}


@app.route('/')
@tab('Packages')
def frontpage():
    return package_view(db.query(Package), "frontpage.html")


@app.route('/package/<name>')
@tab('Packages', slave=True)
def package_detail(name):
    package = db.query(Package)\
                .filter_by(name=name)\
                .options(subqueryload(Package.unapplied_changes))\
                .first_or_404()
    page = db.query(Build)\
             .filter_by(package_id=package.id)\
             .options(subqueryload(Build.dependency_changes),
                      subqueryload(Build.build_arch_tasks))\
             .order_by(Build.id.desc())\
             .paginate(builds_per_page)

    return render_template("package-detail.html", package=package, page=page,
                           builds=page.items)


@app.route('/package/<name>/<int:build_id>')
@tab('Packages', slave=True)
def build_detail(name, build_id):
    # pylint: disable=E1101
    build = db.query(Build)\
              .options(joinedload(Build.package),
                       subqueryload(Build.dependency_changes),
                       subqueryload(Build.build_arch_tasks))\
              .filter_by(id=build_id).first()
    if not build or build.package.name != name:
        abort(404)
    return render_template("build-detail.html", build=build)


@app.route('/groups')
@tab('Groups')
def groups_overview():
    groups = db.query(PackageGroup)\
               .options(undefer(PackageGroup.package_count))\
               .order_by(PackageGroup.name).all()
    return render_template("groups.html", groups=groups)


@app.route('/groups/<int:id>')
@app.route('/groups/<name>')
@tab('Group', slave=True)
def group_detail(name=None, id=None):
    filt = {'name': name} if name else {'id': id}
    group = db.query(PackageGroup)\
              .filter_by(**filt).first_or_404()

    query = db.query(Package)\
              .outerjoin(PackageGroupRelation)\
              .filter(PackageGroupRelation.group_id == group.id)

    return package_view(query, "group-detail.html", group=group)


@app.route('/user/<name>')
@tab('Packages', slave=True)
def user_packages(name):
    if g.user and name == g.user.name:
        g.current_tab = 'my_packages'
    user = get_or_create(db, User, name=name)
    plugin.dispatch_event('refresh_user_packages', user=user)
    query = db.query(Package)\
              .outerjoin(UserPackageRelation)\
              .filter(UserPackageRelation.user_id == user.id)
    untracked = request.args.get('untracked') == '1'

    return package_view(query, "user-packages.html", keep_ignored=untracked,
                        user=user, untracked=untracked)

@app.route('/user/<name>/resync')
def resync_packages(name):
    user = get_or_create(db, User, name=name)
    user.packages_retrieved = False
    db.commit()
    return redirect(url_for('user_packages', name=name))

def process_group_form(group=None, success_msg="Group updated"):
    def redir(msg):
        flash(msg)
        if group:
            return redirect(url_for('edit_group', name=group.name))
        return redirect(url_for('add_group'))
    pkg_names = [name.strip() for name in request.form['packages'].split()]
    packages = db.query(Package)\
                 .filter(Package.name.in_(pkg_names)).all()
    name = request.form['name'].strip()
    if not name:
        return redir("Empty name not allowed")
    if not pkg_names:
        return redir("Empty group not allowed")
    if len(pkg_names) != len(packages):
        return redir("Package doesn't exist")
    # TODO validation
    if not group:
        group = PackageGroup(name=name)
        db.add(group)
        db.flush()
    else:
        group.name = name
        db.query(PackageGroupRelation)\
          .filter_by(group_id=group.id).delete()
    pkg_names = [name.strip() for name in request.form['packages'].split()]
    # TODO bulk insert
    for pkg in packages:
        rel = PackageGroupRelation(group_id=group.id, package_id=pkg.id)
        db.add(rel)
    db.commit()
    flash(success_msg)
    return redirect(url_for('group_detail', name=group.name))


@app.route('/add_group', methods=['GET', 'POST'])
@tab('Group', slave=True)
@auth.login_required()
def add_group(name=None, id=None):
    if request.method == 'POST':
        return process_group_form(success_msg="Group created")
    return render_template("edit-group.html")


@app.route('/groups/<int:id>/edit', methods=['GET', 'POST'])
@app.route('/groups/<name>/edit', methods=['GET', 'POST'])
@tab('Group', slave=True)
@auth.login_required()
def edit_group(name=None, id=None):
    filt = {'name': name} if name else {'id': id}
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(**filt).first_or_404()
    if request.method == 'POST':
        return process_group_form(group)
    return render_template("edit-group.html", group=group)


@app.route('/add_packages', methods=['GET', 'POST'])
@tab('Add packages')
@auth.login_required()
def add_packages():
    if request.method == 'POST':
        be = create_backend()
        names = re.split(r'[ \t\n\r,]+', request.form['names'])
        if names:
            try:
                added = be.add_packages(names,
                                        group=request.form.get('group') or None)
                if added:
                    added = ' '.join(x.name for x in added)
                    log.info("{user} added\n{added}".format(user=g.user.name,
                                                            added=added))
                    flash("Packages added: {added}".format(added=added))
                else:
                    flash("Given packages already present")
                db.commit()
            except backend.PackagesDontExist as e:
                flash("Packages don't exist: " + ','.join(e.names))
                return redirect(url_for('add_packages'))
            return redirect(request.form.get('next') or url_for('frontpage'))
    all_groups = db.query(PackageGroup)\
                   .order_by(PackageGroup.name).all()
    return render_template("add-packages.html", all_groups=all_groups)


@app.route('/documentation')
@tab('Documentation')
def documentation():
    return render_template("documentation.html")


@app.route('/search')
@tab('Packages', slave=True)
def search():
    term = request.args.get('q')
    if term:
        matcher = '%{}%'.format(term.strip().replace('*', '%'))
        query = db.query(Package).filter(Package.name.ilike(matcher))
        return package_view(query, "search-results.html")
    return redirect(url_for('frontpage'))

@app.route('/edit_package', methods=['POST'])
@auth.login_required()
def edit_package():
    form = request.form
    package = db.query(Package)\
                .filter_by(name=form['package']).first_or_404()
    try:
        if 'manual_priority' in form:
            new_priority = int(form['manual_priority'])
            package.manual_priority = new_priority
            flash("Manual priority changed to {}".format(new_priority))
        if 'arch_override' in form:
            package.arch_override = form['arch_override'].strip() or None
            flash("Arch override changed to {}".format(package.arch_override))
    except (KeyError, ValueError):
        abort(400)

    db.commit()
    return redirect(url_for('package_detail', name=form['package']))

@app.route('/bugreport/<name>')
def bugreport(name):
    session = util.create_koji_session(anonymous=True)
    srpm, _ = (util.get_last_srpm(session, name) or abort(404))
    template = util.config['bugreport']['template']
    bug = {key: template[key].format(**srpm) for key in template.keys()}
    bug['comment'] = dedent(bug['comment']).strip()
    query = urllib.urlencode(bug)
    bugreport_url = util.config['bugreport']['url'] % query
    return redirect(bugreport_url)
