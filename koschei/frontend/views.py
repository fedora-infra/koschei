# Copyright (C) 2014-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import logging
import re
import urllib
from datetime import datetime
from functools import wraps
from textwrap import dedent

from flask import abort, render_template, request, url_for, redirect, g, flash
from flask_wtf import Form
from jinja2 import Markup, escape
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, subqueryload, undefer, contains_eager, aliased
from sqlalchemy.sql import exists, func, false
from wtforms import StringField, TextAreaField
from wtforms.validators import Regexp, ValidationError

from koschei import util, plugin, data
from koschei.config import get_config
from koschei.frontend import app, db, frontend_config, auth
from koschei.models import (Package, Build, PackageGroup, PackageGroupRelation,
                            AdminNotice, BuildrootProblem, BasePackage,
                            GroupACL, Collection, CollectionGroup)

log = logging.getLogger('koschei.views')

packages_per_page = frontend_config['packages_per_page']
builds_per_page = frontend_config['builds_per_page']

plugin.load_plugins('frontend')


def page_args(clear=False, **kwargs):
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
    unfiltered = kwargs if clear else dict(request.args.items(), **kwargs)
    args = {k: v for k, v in unfiltered.items() if v is not None}
    encoded = urllib.urlencode(args).replace('...', "' + this.value + '")
    if encoded:
        return '?' + encoded
    return ''


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


def generate_links(package):
    output = []
    for link_dict in get_config('links'):
        name = link_dict['name']
        url = link_dict['url']
        try:
            for interp in re.findall(r'\{([^}]+)\}', url):
                if not re.match(r'package\.?', interp):
                    raise RuntimeError("Only 'package' variable can be "
                                       "interpolated into link url")
                value = package
                for part in interp.split('.')[1:]:
                    value = getattr(value, part)
                if value is None:
                    raise AttributeError()  # continue the outer loop
                url = url.replace('{' + interp + '}',
                                  escape(urllib.quote_plus(str(value))))
            output.append('<a href="{url}">{name}</a>'.format(name=name, url=url))
        except AttributeError:
            continue
    return Markup('\n'.join(output))


def columnize(what, css_class=None):
    attrs = ' class="{}"'.format(css_class) if css_class else ''
    return Markup('\n'.join('<td{}>{}</td>'.format(attrs, escape(item))
                            for item in what))


def get_global_notices():
    notices = [n.content for n in
               db.query(AdminNotice.content).filter_by(key="global_notice")]
    for collection in g.current_collections:
        if collection.latest_repo_resolved is False:
            problems = db.query(BuildrootProblem)\
                .filter_by(collection_id=collection.id).all()
            notices.append("Base buildroot for {} is not installable. "
                           "Dependency problems:<br/>".format(collection) +
                           '<br/>'.join((p.problem for p in problems)))
    notices = map(Markup, notices)
    return notices


def require_login():
    return " " if g.user else ' disabled="true" '


def secondary_koji_url(collection):
    if collection.secondary_mode:
        return get_config('secondary_koji_config.weburl')
    return get_config('koji_config.weburl')


app.jinja_env.globals.update(
    primary_koji_url=get_config('koji_config.weburl'),
    secondary_koji_url=secondary_koji_url,
    generate_links=generate_links,
    inext=next, iter=iter,
    min=min, max=max, page_args=page_args,
    get_global_notices=get_global_notices,
    require_login=require_login,
    Package=Package, Build=Build,
    auto_tracking=frontend_config['auto_tracking'])

app.jinja_env.filters.update(columnize=columnize,
                             format_depchange=format_depchange)


class Reversed(object):
    def __init__(self, content):
        self.content = content

    def desc(self):
        return self.content

    def asc(self):
        return self.content.desc()


class NullsLastOrder(Reversed):
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


def populate_package_groups(packages):
    base_map = {}
    for package in packages:
        package.visible_groups = []
        base_map[package.base_id] = package
    filter_expr = PackageGroup.namespace == None
    if g.user:
        filter_expr |= GroupACL.user_id == g.user.id
    query = db.query(PackageGroupRelation)\
        .options(contains_eager(PackageGroupRelation.group))\
        .filter(PackageGroupRelation.base_id.in_(base_map.keys()))\
        .join(PackageGroup)\
        .filter(filter_expr)\
        .order_by(PackageGroup.namespace, PackageGroup.name)
    if g.user:
        query = query.outerjoin(GroupACL)
    for r in query:
        base_map[r.base_id].visible_groups.append(r.group)


def package_view(template, query_fn=None, **template_args):
    if len(g.current_collections) == 1:
        return collection_package_view(template, query_fn, **template_args)
    return unified_package_view(template, query_fn, **template_args)


def collection_package_view(template, query_fn=None, **template_args):
    collection = g.current_collections[0]
    package_query = db.query(Package).filter(Package.collection_id == collection.id)
    if query_fn:
        package_query = query_fn(package_query.join(BasePackage))
    untracked = request.args.get('untracked') == '1'
    order_name = request.args.get('order_by', 'running,state,name')
    # pylint: disable=E1101
    order_map = {'name': [Package.name],
                 'state': [Package.resolved, Reversed(Build.state)],
                 'running': [Package.last_complete_build_id == Package.last_build_id],
                 'task_id': [Build.task_id],
                 'started': [Build.started],
                 'current_priority': [NullsLastOrder(Package.current_priority)]}
    order_names, order = get_order(order_map, order_name)

    if not untracked:
        package_query = package_query.filter(Package.tracked == True)
    pkgs = package_query.filter(Package.blocked == False)\
                        .outerjoin(Package.last_build)\
                        .options(contains_eager(Package.last_build))\
                        .order_by(*order)
    page = pkgs.paginate(packages_per_page)
    populate_package_groups(page.items)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, collection=collection,
                           **template_args)


def state_icon(package):
    icon = {'ok': 'complete',
            'failing': 'failed',
            'unresolved': 'cross',
            'blocked': 'unknown',
            'untracked': 'unknown'}.get(package.state_string, 'unknown')
    return url_for('static', filename='images/{name}.png'.format(name=icon))
Package.state_icon = property(state_icon)


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


@app.before_request
def get_collections():
    if request.endpoint == 'static':
        return
    collection_name = request.args.get('collection')
    g.collections = db.query(Collection)\
        .order_by(Collection.order, Collection.name.desc())\
        .all()
    for collection in g.collections:
        db.expunge(collection)
    if not g.collections:
        abort(500, "No collections setup")
    g.collections_by_name = {c.name: c for c in g.collections}
    g.current_collections = []
    if collection_name:
        try:
            for component in collection_name.split(','):
                g.current_collections.append(g.collections_by_name[component])
        except KeyError:
            abort(404, "Collection not found")
    else:
        g.current_collections = g.collections


class UnifiedPackage(object):
    def __init__(self, row):
        self.name = row.name
        self.has_running_build = row.has_running_build
        self.base_id = row.base_id
        self.packages = []
        for collection in g.current_collections:
            str_id = str(collection.id)
            package = Package(
                name=row.name,
                blocked=False,
                collection=collection,
                tracked=getattr(row, 'tracked' + str_id) or False,
                last_complete_build_state=getattr(row, 'state' + str_id),
                resolved=getattr(row, 'resolved' + str_id),
            )
            self.packages.append(package)


def unified_package_view(template, query_fn=None, **template_args):
    untracked = request.args.get('untracked') == '1'
    order_name = request.args.get('order_by', 'running,failing,name')
    exprs = []
    tables = []
    running_build_expr = false()
    failing_expr = false()
    tracked_expr = false()
    order_map = {'name': [BasePackage.name]}
    for collection in g.current_collections:
        table = aliased(Package)
        tables.append(table)
        exprs.append(table.tracked.label('tracked{}'.format(collection.id)))
        exprs.append(table.resolved.label('resolved{}'.format(collection.id)))
        exprs.append(table.last_complete_build_state
                     .label('state{}'.format(collection.id)))
        running_build_expr |= table.last_build_id != table.last_complete_build_id
        failing_expr |= table.last_complete_build_state == Build.FAILED
        failing_expr |= table.resolved == False
        tracked_expr |= table.tracked == True
    running_build_expr = func.coalesce(running_build_expr, false())
    failing_expr = func.coalesce(failing_expr, false())
    query = db.query(BasePackage.name, BasePackage.id.label('base_id'),
                     running_build_expr.label('has_running_build'),
                     *exprs).filter(~BasePackage.all_blocked)
    if not untracked:
        query = query.filter(tracked_expr)
    for collection, table in zip(g.current_collections, tables):
        on_expr = BasePackage.id == table.base_id
        on_expr &= table.collection_id == collection.id
        on_expr &= ~table.blocked
        if not untracked:
            on_expr &= table.tracked
        query = query.outerjoin(table, on_expr)
        order_map['state-' + collection.name] = \
            [table.resolved, Reversed(table.last_complete_build_state)]
    if query_fn:
        query = query_fn(query)
    order_map['running'] = [Reversed(running_build_expr)]
    order_map['failing'] = [Reversed(failing_expr)]

    order_names, order = get_order(order_map, order_name)

    page = query.order_by(*order).paginate(packages_per_page)
    page.items = map(UnifiedPackage, page.items)
    populate_package_groups(page.items)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, collection=None, **template_args)


@app.route('/collections')
@tab('Collections')
def collection_list():
    groups = db.query(CollectionGroup)\
        .options(joinedload(CollectionGroup.collections))\
        .all()
    categorized_ids = {c.id for g in groups for c in g.collections}
    uncategorized = [c for c in g.collections if c.id not in categorized_ids]
    return render_template("list-collections.html", groups=groups,
                           uncategorized=uncategorized)


@app.route('/packages')
@tab('Packages')
def package_list():
    return package_view("list-packages.html")


@app.route('/')
@tab('Packages', slave=True)
def frontpage():
    return app.view_functions[frontend_config['frontpage']](
        **frontend_config['frontpage_kwargs']
    )


@app.route('/package/<name>')
@tab('Packages', slave=True)
def package_detail(name):
    collection = g.current_collections[0]
    packages = {p.collection_id: p for p in db.query(Package).filter_by(name=name)}
    package = None
    all_packages = []
    for coll in g.collections:
        p = packages.get(coll.id)
        if p:
            all_packages.append((coll, p))
            if coll is collection:
                package = p
    if not package:
        abort(404)
    package.global_groups = db.query(PackageGroup)\
        .join(PackageGroupRelation)\
        .filter(PackageGroupRelation.base_id == package.base_id)\
        .filter(PackageGroup.namespace == None)\
        .all()
    package.user_groups = []
    package.available_groups = []
    if g.user:
        user_groups = \
            db.query(PackageGroup,
                     func.bool_or(PackageGroupRelation.base_id == package.base_id))\
            .outerjoin(PackageGroupRelation)\
            .join(GroupACL)\
            .filter(GroupACL.user_id == g.user.id)\
            .order_by(PackageGroup.namespace.nullsfirst(), PackageGroup.name)\
            .group_by(PackageGroup.id)\
            .distinct().all()
        package.user_groups = [group for group, checked in user_groups if
                               checked and group.namespace]
        package.available_groups = [group for group, checked in user_groups if
                                    not checked]
    page = db.query(Build)\
             .filter_by(package_id=package.id)\
             .options(subqueryload(Build.dependency_changes),
                      subqueryload(Build.build_arch_tasks))\
             .order_by(Build.id.desc())\
             .paginate(builds_per_page)

    return render_template("package-detail.html", package=package, page=page,
                           builds=page.items, all_packages=all_packages)


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
    if not g.user.admin:
        abort(403)
    build = db.query(Build).filter_by(id=build_id).first_or_404()
    if EmptyForm().validate_or_flash():
        if build.state != Build.RUNNING:
            flash("Only running builds can be canceled.")
        elif build.cancel_requested:
            flash("Build already has pending cancelation request.")
        else:
            flash("Cancelation request sent.")
            build.cancel_requested = True
            db.commit()
    return redirect(url_for('package_detail', name=build.package.name))


@app.route('/groups')
@tab('Groups')
def groups_overview():
    groups = db.query(PackageGroup)\
               .options(undefer(PackageGroup.package_count))\
               .filter_by(namespace=None)\
               .order_by(PackageGroup.name).all()
    return render_template("list-groups.html", groups=groups)


@app.route('/groups/<name>')
@app.route('/groups/<namespace>/<name>')
@tab('Group', slave=True)
def group_detail(name=None, namespace=None):
    group = db.query(PackageGroup)\
              .filter_by(name=name, namespace=namespace).first_or_404()

    def query_fn(query):
        return query.outerjoin(PackageGroupRelation,
                               PackageGroupRelation.base_id == BasePackage.id)\
            .filter(PackageGroupRelation.group_id == group.id)

    return package_view("group-detail.html", query_fn=query_fn, group=group)


@app.route('/user/<name>')
@tab('Packages', slave=True)
def user_packages(name):
    names = []
    try:
        for res in plugin.dispatch_event('get_user_packages', username=name):
            if res:
                names += res
    except Exception:
        flash("Error retrieving user's packages")
        log.exception("Error retrieving user's packages")

    def query_fn(query):
        return query.filter(BasePackage.name.in_(names))

    return package_view("user-packages.html", query_fn, username=name)


class StrippedStringField(StringField):
    def process_formdata(self, values):
        # pylint:disable=W0201
        self.data = values and values[0].strip()


class ListFieldMixin(object):
    split_re = re.compile(r'[ \t\n\r,]+')

    def process_formdata(self, values):
        # pylint:disable=W0201
        values = values and values[0]
        self.data = filter(None, self.split_re.split(values or ''))


class ListField(ListFieldMixin, StringField):
    def _value(self):
        return ', '.join(self.data or ())


class ListAreaField(ListFieldMixin, TextAreaField):
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
    owners = ListField('owners', [NonEmptyList("Group must have an owner"),
                                  NameListValidator("Invalid owner list")])


class AddPackagesForm(EmptyForm):
    packages = ListAreaField('packages', [NonEmptyList("No packages given"),
                                          NameListValidator("Invalid package list")])
    collection = StrippedStringField('collection')
    group = StrippedStringField('group', [Regexp(group_re, message="Invalid group")])


def can_edit_group(group):
    return g.user and (g.user.admin or
                       db.query(exists()
                                .where((GroupACL.user_id == g.user.id) &
                                       (GroupACL.group_id == group.id)))
                       .scalar())
PackageGroup.editable = property(can_edit_group)


def process_group_form(group=None):
    if request.method == 'GET':
        if group:
            obj = dict(name=group.name, owners=[u.name for u in group.owners],
                       packages=[p.name for p in group.packages])
            form = GroupForm(**obj)
        else:
            form = GroupForm(owners=[g.user.name])
        return render_template('edit-group.html', group=group, form=form)
    form = GroupForm()
    # check permissions
    if group and not group.editable:
        flash("You don't have permission to edit this group")
        return redirect(url_for('group_detail', name=group.name,
                                namespace=group.namespace))
    # check form validity
    if not form.validate_or_flash():
        return render_template('edit-group.html', group=group, form=form)

    created = not group
    if created:
        group = PackageGroup(namespace=g.user.name)
        db.add(group)
    group.name = form.name.data
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash("Group already exists")
        return render_template('edit-group.html', group=group, form=form)
    try:
        data.set_group_content(db, group, form.packages.data)
        data.set_group_maintainers(db, group, form.owners.data)
    except data.PackagesDontExist as e:
        db.rollback()
        flash(str(e))
        return render_template('edit-group.html', group=group, form=form)
    db.commit()
    flash("Group created" if created else "Group modified")
    return redirect(url_for('group_detail', name=group.name,
                            namespace=group.namespace))


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


@app.route('/groups/<name>/delete', methods=['GET', 'POST'])
@app.route('/groups/<namespace>/<name>/delete', methods=['GET', 'POST'])
@auth.login_required()
def delete_group(name, namespace=None):
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(name=name, namespace=namespace).first_or_404()
    if request.method == 'POST':
        if EmptyForm().validate_or_flash() and group.editable:
            db.delete(group)
            db.commit()
            return redirect(url_for('groups_overview'))
        return render_template('edit-group.html', group=group)
    return redirect(url_for('groups_overview'))


if not frontend_config['auto_tracking']:
    @app.route('/add_packages', methods=['GET', 'POST'])
    @tab('Add packages')
    @auth.login_required()
    def add_packages():
        form = AddPackagesForm()
        if request.method == 'POST':
            if not form.validate_or_flash():
                return render_template("add-packages.html", form=form)
            names = set(form.packages.data)
            try:
                collection = [c for c in g.collections
                              if c.name == form.collection.data][0]
            except IndexError:
                abort(404)

            if form.group.data:
                name, namespace = PackageGroup.parse_name(form.group.data)
                group = db.query(PackageGroup)\
                          .filter_by(namespace=namespace, name=name)\
                          .first_or_404()
                if not group.editable:
                    abort(400)
                data.set_group_content(db, group, names, append=True)

            try:
                added = data.track_packages(db, collection, names)
            except data.PackagesDontExist as e:
                db.rollback()
                flash(str(e))
                return render_template("add-packages.html", form=form)

            flash("Packages added: {}".format(','.join(p.name for p in added)))
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

        def query_fn(query):
            return query.filter(BasePackage.name.ilike(matcher))
        return package_view("search-results.html", query_fn)
    return redirect(url_for('frontpage'))


@app.route('/package/<name>/edit', methods=['POST'])
@auth.login_required()
def edit_package(name):
    form = request.form
    try:
        collection = g.collections_by_name[form['collection']]
        package = db.query(Package)\
            .filter_by(name=name, collection_id=collection.id)\
            .first_or_404()
        for key, prev_val in form.items():
            if key.startswith('group-prev-'):
                group = db.query(PackageGroup).get_or_404(int(key[len('group-prev-'):]))
                new_val = form.get('group-{}'.format(group.id))
                if bool(new_val) != (prev_val == 'true'):
                    if not group.editable:
                        abort(403)
                    if new_val:
                        rel = PackageGroupRelation(package_name=package.name,
                                                   group_id=group.id)
                        db.add(rel)
                    else:
                        db.query(PackageGroupRelation)\
                            .filter_by(group_id=group.id, package_name=package.name)\
                            .delete(synchronize_session=False)
        if 'manual_priority' in form:
            new_priority = int(form['manual_priority'])
            package.manual_priority = new_priority
        if 'arch_override' in form:
            package.arch_override = form['arch_override'].strip() or None
        flash("Package modified")
    except (KeyError, ValueError):
        abort(400)

    db.commit()
    return redirect(url_for('package_detail', name=package.name) +
                    "?collection=" + collection.name)


@app.route('/bugreport/<name>')
def bugreport(name):
    package = db.query(Package)\
                .filter(Package.name == name)\
                .filter(Package.blocked == False)\
                .filter(Package.last_complete_build_id != None)\
                .options(joinedload(Package.last_complete_build))\
                .first() or abort(404)
    variables = package.srpm_nvra or abort(404)
    variables['package'] = package
    variables['collection'] = package.collection
    variables['url'] = request.url_root.replace(request.script_root, '').rstrip('/') \
        + url_for('package_detail', name=package.name)
    template = get_config('bugreport.template')
    bug = {key: template[key].format(**variables) for key in template.keys()}
    bug['comment'] = dedent(bug['comment']).strip()
    query = urllib.urlencode(bug)
    bugreport_url = get_config('bugreport.url').format(query=query)
    return redirect(bugreport_url)


@app.route('/collection/<name>')
def collection_detail(name):
    for collection in g.collections:
        if collection.name == name:
            return render_template("collection-detail.html",
                                   collection=collection)
    abort(404)


@app.route('/collection/<name>/edit', methods=['POST'])
@auth.login_required()
def edit_collection(name):
    if not g.user.admin:
        abort(403)
    # Not implemented
    abort(501)
