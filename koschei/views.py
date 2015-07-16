import re
import koji
import urllib
import logging

from datetime import datetime
from functools import wraps
from flask import abort, render_template, request, url_for, redirect, g, flash
from sqlalchemy.orm import joinedload, subqueryload, undefer, contains_eager
from sqlalchemy.sql import exists
from sqlalchemy.exc import IntegrityError
from jinja2 import Markup, escape
from textwrap import dedent
from flask_wtf import Form
from wtforms import StringField
from wtforms.validators import Regexp, ValidationError

from .models import (Package, Build, PackageGroup, PackageGroupRelation,
                     AdminNotice, User, UserPackageRelation, BuildrootProblem,
                     GroupACL, get_or_create, get_last_repo)
from . import util, auth, backend, main, plugin
from .frontend import app, db, frontend_config

log = logging.getLogger('koschei.views')

packages_per_page = frontend_config['packages_per_page']
builds_per_page = frontend_config['builds_per_page']

main.load_globals()

def create_backend():
    return backend.Backend(db=db, log=log,
                           koji_session=util.create_koji_session(anonymous=True))


def page_args(**kwargs):
    def proc_order(order):
        new_order = []
        for item in order:
            if (item.replace('-', '')
                    not in new_order and '-' + item not in new_order):
                new_order.append(item)
        return ','.join(new_order)
    if 'order_by' in kwargs:
        kwargs['order_by'] = proc_order(kwargs['order_by'])
    # the supposedly unnecessary call to items() is needed
    args = {k: v for k, v in dict(request.args.items(), **kwargs).items()
            if v is not None}
    return urllib.urlencode(args)


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

def get_global_notices():
    notices = db.query(AdminNotice).filter_by(key="global_notice").all()
    repo = get_last_repo(db)
    if repo and repo.base_resolved is False:
        problems = db.query(BuildrootProblem).filter_by(repo_id=repo.repo_id).all()
        notices.append("Base buildroot is not installable. Operation suspended. "
                       "Dependency problems: " + ', '.join((p.problem for p in problems)))
    return notices


def require_login():
    return " " if g.user else ' disabled="true" '


pathinfo = koji.PathInfo(topdir=util.koji_config['topurl'])
app.jinja_env.globals.update(koji_weburl=util.config['koji_config']['weburl'],
                             koji_pathinfo=pathinfo, inext=next, iter=iter,
                             min=min, max=max, page_args=page_args,
                             get_global_notices=get_global_notices,
                             require_login=require_login)

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
                order = [o.desc() for o in order_map.get(component[1:], ())]
            else:
                order = [o.asc() for o in order_map.get(component, ())]
            orders.extend(order)
    if any(order is None for order in orders):
        abort(400)
    return components, orders


def package_view(package_query, template, **template_args):
    order_name = request.args.get('order_by', 'name')
    # pylint: disable=E1101
    order_map = {'name': [Package.name],
                 'state': [Package.resolved, Reversed(Build.state)],
                 'task_id': [Build.task_id],
                 'started': [Build.started],
                 'current_priority': [PriorityOrder(Package.current_priority)]}
    order_names, order = get_order(order_map, order_name)
    pkgs = (package_query if template_args.get('untracked')
            else package_query.filter(Package.tracked == True))\
             .filter(Package.blocked == False)\
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
def inject_links():
    return {'links': util.config['links']}

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


@app.route('/build/<int:build_id>')
@tab('Packages', slave=True)
def build_detail(build_id):
    # pylint: disable=E1101
    build = db.query(Build)\
              .options(joinedload(Build.package),
                       subqueryload(Build.dependency_changes),
                       subqueryload(Build.build_arch_tasks))\
              .filter_by(id=build_id).first_or_404()
    return render_template("build-detail.html", build=build,
                           cancel_form=EmptyForm())


@app.route('/build/<int:build_id>/cancel', methods=['POST'])
@tab('Packages', slave=True)
@auth.login_required()
def cancel_build(build_id):
    build = db.query(Build).filter_by(id=build_id).first_or_404()
    if EmptyForm().validate_or_flash():
        try:
            util.create_koji_session(anonymous=False).cancelTask(build.task_id)
            flash("Cancelation request sent.")
        except Exception:
            flash("Error in communication with Koji. Please try again later.")
    return redirect(url_for('package_detail', name=build.package.name))


@app.route('/groups')
@tab('Groups')
def groups_overview():
    groups = db.query(PackageGroup)\
               .options(undefer(PackageGroup.package_count))\
               .filter_by(namespace=None)\
               .order_by(PackageGroup.name).all()
    return render_template("groups.html", groups=groups)


@app.route('/groups/<name>')
@app.route('/groups/<namespace>/<name>')
@tab('Group', slave=True)
def group_detail(name=None, namespace=None):
    group = db.query(PackageGroup)\
              .filter_by(name=name, namespace=namespace).first_or_404()

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
    db.commit()
    plugin.dispatch_event('refresh_user_packages', user=user)
    query = db.query(Package)\
              .outerjoin(UserPackageRelation)\
              .filter(UserPackageRelation.user_id == user.id)
    untracked = request.args.get('untracked') == '1'

    return package_view(query, "user-packages.html", user=user, untracked=untracked)

@app.route('/user/<name>/resync')
@auth.login_required()
def resync_packages(name):
    user = get_or_create(db, User, name=name)
    user.packages_retrieved = False
    db.commit()
    return redirect(url_for('user_packages', name=name))


class StrippedStringField(StringField):
    def process_formdata(self, values):
        # pylint:disable=W0201
        self.data = values and values[0].strip()


class ListField(StringField):
    split_re = re.compile(r'[ \t\n\r,]+')

    def _value(self):
        return ', '.join(self.data or ())

    def process_formdata(self, values):
        # pylint:disable=W0201
        values = values and values[0]
        self.data = filter(None, self.split_re.split(values or ''))

class ListAreaField(ListField):
    def _value(self):
        return '\n'.join(self.data or ())

name_re = re.compile(r'^[a-zA-Z0-9.+_-]+$')
group_re = re.compile(r'^([a-zA-Z0-9.+_-]+(/[a-zA-Z0-9.+_-]+)?)?$')

class NameListValidator(object):
    def __init__(self, message):
        self.message = message

    def __call__(self, _, field):
        if not all(map(name_re.match, field.data)):
            raise ValidationError(self.message)

class NonEmptyList(object):
    def __init__(self, message):
        self.message = message

    def __call__(self, _, field):
        if not field.data:
            raise ValidationError(self.message)

class EmptyForm(Form):
    def validate_or_flash(self):
        if self.validate_on_submit():
            return True
        flash(', '.join(x for i in self.errors.values() for x in i))
        return False

class GroupForm(EmptyForm):
    name = StrippedStringField('name', [Regexp(name_re, message="Invalid group name")])
    packages = ListAreaField('packages', [NonEmptyList("Empty group not allowed"),
                                          NameListValidator("Invalid package list")])
    owners = ListField('owners', [NameListValidator("Invalid owner list")])

class AddPackagesForm(EmptyForm):
    packages = ListAreaField('packages', [NonEmptyList("No packages given"),
                                          NameListValidator("Invalid package list")])
    group = StrippedStringField('group', [Regexp(group_re, message="Invalid group")])

def can_edit_group(group):
    return g.user and (g.user.admin or
                       db.query(exists()
                                .where((GroupACL.user_id == g.user.id) &
                                       (GroupACL.group_id == group.id)))
                       .scalar())

PackageGroup.editable = property(can_edit_group)

def process_group_form(group=None):
    form = GroupForm()
    if request.method == 'GET':
        return render_template('edit-group.html', group=group, form=form)
    if not form.validate_or_flash():
        return render_template('edit-group.html', group=group, form=form)
    be = create_backend()
    names = set(form.packages.data)
    owners = set(form.owners.data)
    if group.namespace:
        owners.add(group.namespace)
    user_ids = [get_or_create(db, User, name=name).id for name in owners]
    db.commit()
    try:
        be.add_packages(names)
    except backend.PackagesDontExist as e:
        flash("Packages don't exist: " + ', '.join(e.names))
        return render_template('edit-group.html', group=group, form=form)
    packages = db.query(Package).filter(Package.name.in_(names))
    created = not group
    try:
        if not group:
            group = PackageGroup(name=form.name.data, namespace=g.user.name)
            db.add(group)
            db.flush()
        elif group.editable:
            group.name = form.name.data
            db.query(PackageGroupRelation)\
              .filter_by(group_id=group.id).delete()
            db.query(GroupACL)\
              .filter_by(group_id=group.id).delete()
        else:
            flash("You don't have permission to edit this group")
            redirect(url_for('group_detail', name=group.name))
        rels = [dict(group_id=group.id, package_id=pkg.id) for pkg in packages]
        acls = [dict(group_id=group.id, user_id=user_id) for user_id in user_ids]
        db.execute(PackageGroupRelation.__table__.insert(), rels)
        if acls:
            db.execute(GroupACL.__table__.insert(), acls)
        db.commit()
        flash("Group created" if created else "Group modified")
        return redirect(url_for('group_detail', name=group.name,
                                namespace=group.namespace))
    except IntegrityError:
        db.rollback()
        flash("Group already exists")
        return render_template('edit-group.html', group=group, form=form)


@app.route('/add_group', methods=['GET', 'POST'])
@tab('Group', slave=True)
@auth.login_required()
def add_group():
    return process_group_form()


@app.route('/groups/<name>/edit', methods=['GET', 'POST'])
@app.route('/groups/<namespace>/<name>/edit', methods=['GET', 'POST'])
@tab('Group', slave=True)
@auth.login_required()
def edit_group(name, namespace=None):
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(name=name, namespace=namespace).first_or_404()
    return process_group_form(group=group)


@app.route('/add_packages', methods=['GET', 'POST'])
@tab('Add packages')
@auth.login_required()
def add_packages():
    form = AddPackagesForm()
    if request.method == 'POST':
        be = create_backend()
        if not form.validate_or_flash():
            return render_template("add-packages.html", form=form)
        names = set(form.packages.data)
        try:
            added = be.add_packages(names)
        except backend.PackagesDontExist as e:
            flash("Packages don't exist: " + ','.join(e.names))
            return render_template("add-packages.html", form=form)
        if form.group.data:
            name, _, namespace = reversed(form.group.data.partition('/'))
            group = db.query(PackageGroup)\
                      .filter_by(namespace=namespace or None, name=name)\
                      .first() or abort(400)
            if not group.editable:
                abort(400)
            subq = db.query(PackageGroupRelation.package_id)\
                     .filter_by(group_id=group.id).subquery()
            packages = db.query(Package)\
                         .filter(Package.name.in_(names))\
                         .filter(Package.id.notin_(subq)).all()
            rels = [dict(group_id=group.id, package_id=pkg.id) for pkg in packages]
            if rels:
                db.execute(PackageGroupRelation.__table__.insert(), rels)
        if added:
            added = ' '.join(x.name for x in added)
            log.info("{user} added\n{added}".format(user=g.user.name,
                                                    added=added))
            flash("Packages added: {added}".format(added=added))
        db.commit()
        return redirect(request.form.get('next') or url_for('frontpage'))
    return render_template("add-packages.html", form=form)


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
    try:
        package = db.query(Package)\
                    .filter_by(name=form['package']).first_or_404()
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
@auth.login_required()
def bugreport(name):
    session = util.create_koji_session(anonymous=True)
    srpm, _ = (util.get_last_srpm(session, name) or abort(404))
    template = util.config['bugreport']['template']
    bug = {key: template[key].format(**srpm) for key in template.keys()}
    bug['comment'] = dedent(bug['comment']).strip()
    query = urllib.urlencode(bug)
    bugreport_url = util.config['bugreport']['url'] % query
    return redirect(bugreport_url)
