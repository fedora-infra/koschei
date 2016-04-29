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
from sqlalchemy.orm import joinedload, subqueryload, undefer, contains_eager
from sqlalchemy.sql import exists, func
from wtforms import StringField, TextAreaField
from wtforms.validators import Regexp, ValidationError

from koschei import util, plugin
from koschei.config import get_config
from koschei.frontend import app, db, frontend_config, auth
from koschei.models import (Package, Build, PackageGroup, PackageGroupRelation,
                            AdminNotice, User, BuildrootProblem,
                            GroupACL, Collection, get_or_create)

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
    if kwargs.get('collection') == g.default_collection.name:
        kwargs.pop('collection')
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
    if g.current_collection.latest_repo_resolved is False:
        problems = db.query(BuildrootProblem)\
            .filter_by(collection_id=g.current_collection.id).all()
        notices.append("Base buildroot for {} is not installable. "
                       "Dependency problems:<br/>".format(g.current_collection) +
                       '<br/>'.join((p.problem for p in problems)))
    notices = map(Markup, notices)
    return notices


def require_login():
    return " " if g.user else ' disabled="true" '


app.jinja_env.globals.update(
    primary_koji_url=get_config('koji_config.weburl'),
    secondary_koji_url=get_config('secondary_koji_config.weburl'),
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
    package_query = package_query.filter(Package.collection_id == g.current_collection.id)
    untracked = request.args.get('untracked') == '1'
    order_name = request.args.get('order_by', 'running,state,name')
    # pylint: disable=E1101
    order_map = {'name': [Package.name],
                 'state': [Package.resolved, Reversed(Build.state)],
                 'running': [Package.last_complete_build_id == Package.last_build_id],
                 'task_id': [Build.task_id],
                 'started': [Build.started],
                 'current_priority': [PriorityOrder(Package.current_priority)]}
    order_names, order = get_order(order_map, order_name)

    if not untracked:
        package_query = package_query.filter(Package.tracked == True)
    pkgs = package_query.filter(Package.blocked == False)\
                        .outerjoin(Package.last_build)\
                        .options(contains_eager(Package.last_build))\
                        .order_by(*order)
    page = pkgs.paginate(packages_per_page)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, **template_args)


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
    collection_name = request.args.get('collection')
    g.collections = db.query(Collection)\
        .order_by(Collection.order, Collection.name.desc())\
        .all()
    if not g.collections:
        abort(500, "No collections setup")
    g.default_collection = g.collections[0]
    if collection_name:
        for collection in g.collections:
            if collection.name == collection_name:
                g.current_collection = collection
                break
        else:
            abort(404, "Collection not found")
    else:
        g.current_collection = g.default_collection


@app.route('/')
@tab('Packages')
def frontpage():
    return package_view(db.query(Package), "frontpage.html")


@app.route('/package/<name>')
@tab('Packages', slave=True)
def package_detail(name):
    package = db.query(Package)\
                .filter_by(name=name, collection_id=g.current_collection.id)\
                .options(subqueryload(Package.unapplied_changes))\
                .first_or_404()
    package.global_groups = db.query(PackageGroup)\
        .join(PackageGroupRelation)\
        .filter(PackageGroupRelation.package_name == package.name)\
        .filter(PackageGroup.namespace == None)\
        .all()
    package.user_groups = []
    package.available_groups = []
    if g.user:
        user_groups = \
            db.query(PackageGroup,
                     func.bool_or(PackageGroupRelation.package_name == package.name))\
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
    return render_template("groups.html", groups=groups)


@app.route('/groups/<name>')
@app.route('/groups/<namespace>/<name>')
@tab('Group', slave=True)
def group_detail(name=None, namespace=None):
    group = db.query(PackageGroup)\
              .filter_by(name=name, namespace=namespace).first_or_404()

    query = db.query(Package)\
              .outerjoin(PackageGroupRelation,
                         PackageGroupRelation.package_name == Package.name)\
              .filter(Package.collection_id == g.current_collection.id)\
              .filter(PackageGroupRelation.group_id == group.id)

    return package_view(query, "group-detail.html", group=group)


@app.route('/user/<name>')
@tab('Packages', slave=True)
def user_packages(name):
    ids = []
    try:
        for res in plugin.dispatch_event('get_user_packages', db=db, username=name,
                                         current_collection=g.current_collection):
            ids += res
    except Exception:
        flash("Error retrieving user's packages")
        log.exception("Error retrieving user's packages")
    query = db.query(Package)\
        .filter(Package.id.in_(ids))

    return package_view(query, "user-packages.html", username=name)


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
    names = set(form.packages.data)
    users = [get_or_create(db, User, name=name) for name in set(form.owners.data)]
    db.commit()
    user_ids = [u.id for u in users]
    packages = db.query(Package).filter(Package.name.in_(names))
    found_names = {p.name for p in packages}
    if len(found_names) != len(names):
        flash("Packages don't exist: " + ', '.join(names - found_names))
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
    if not created:
        db.query(PackageGroupRelation)\
          .filter_by(group_id=group.id).delete()
        db.query(GroupACL)\
          .filter_by(group_id=group.id).delete()
    rels = [dict(group_id=group.id, package_name=name) for name in found_names]
    acls = [dict(group_id=group.id, user_id=user_id) for user_id in user_ids]
    db.execute(PackageGroupRelation.__table__.insert(), rels)
    db.execute(GroupACL.__table__.insert(), acls)
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
            existing = db.query(Package).filter(Package.name.in_(names)).all()
            nonexistent = names - {p.name for p in existing}
            if nonexistent:
                flash("Packages don't exist: " + ','.join(nonexistent))
                return render_template("add-packages.html", form=form)
            db.query(Package).filter(Package.name.in_(names))\
                             .update({'tracked': True})
            if form.group.data:
                name, _, namespace = reversed(form.group.data.partition('/'))
                group = db.query(PackageGroup)\
                          .filter_by(namespace=namespace or None, name=name)\
                          .first() or abort(400)
                if not group.editable:
                    abort(400)
                rels = [dict(group_id=group.id, package_name=name) for name in names]
                if rels:
                    db.execute(PackageGroupRelation.__table__.insert(), rels)
            added = ' '.join(names)
            log.info("%s added %s", g.user.name, added)
            flash("Packages added: {}".format(added))
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


@app.route('/package/<name>/edit', methods=['POST'])
@auth.login_required()
def edit_package(name):
    package = db.query(Package).filter_by(name=name).first_or_404()
    form = request.form
    try:
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
    return redirect(url_for('package_detail', name=package.name))


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
    variables['url'] = request.url_root + url_for('package_detail', name=package.name)
    template = get_config('bugreport.template')
    bug = {key: template[key].format(**variables) for key in template.keys()}
    bug['comment'] = dedent(bug['comment']).strip()
    query = urllib.urlencode(bug)
    bugreport_url = get_config('bugreport.url').format(query=query)
    return redirect(bugreport_url)
