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

from __future__ import print_function, absolute_import

from textwrap import dedent

import six.moves.urllib as urllib
from flask import abort, render_template, request, url_for, redirect, g
from sqlalchemy import Integer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    joinedload, subqueryload, undefer, contains_eager, aliased,
)
from sqlalchemy.sql import exists, func, false, true, cast, union

from koschei import plugin, data
from koschei.config import get_config
from koschei.db import RpmEVR
from koschei.frontend import forms, auth
from koschei.frontend.base import frontend_config, db, app, session
from koschei.frontend.model_additions import package_running_icon
from koschei.frontend.tabs import Tab
from koschei.frontend.util import flash_nak, flash_ack, Reversed, NullsLastOrder, \
    get_order
from koschei.models import (
    Package, Build, PackageGroup, PackageGroupRelation, BasePackage, GroupACL,
    CollectionGroup, AppliedChange, UnappliedChange, ResolutionChange,
    ResourceConsumptionStats, ScalarStats, Dependency,
)

packages_per_page = frontend_config['packages_per_page']
builds_per_page = frontend_config['builds_per_page']


def populate_package_groups(packages):
    base_map = {}
    for package in packages:
        package.visible_groups = []
        base_map[package.base_id] = package
    filter_expr = PackageGroup.namespace == None
    if g.user:
        filter_expr |= GroupACL.user_id == g.user.id
    query = (
        db.query(PackageGroupRelation)
        .options(contains_eager(PackageGroupRelation.group))
        .filter(
            PackageGroupRelation.base_id.in_(base_map.keys())
            if base_map else false()
        )
        .join(PackageGroup)
        .filter(filter_expr)
        .order_by(PackageGroup.namespace, PackageGroup.name)
    )
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
    current_prio_expr = Package.current_priority_expression(
        collection=collection,
        last_build=Build,  # package is outerjoined with last_build
    )
    package_query = db.query(Package, current_prio_expr)\
        .filter(Package.collection_id == collection.id)
    if query_fn:
        package_query = query_fn(package_query.join(BasePackage))
    untracked = request.args.get('untracked') == '1'
    order_name = request.args.get('order_by', 'running,state,name')
    order_map = {
        'name': [Package.name],
        'state': [Package.resolved, Reversed(Build.state)],
        'running': [Package.last_complete_build_id == Package.last_build_id],
        'task_id': [Build.task_id],
        'started': [Build.started],
        'current_priority': [NullsLastOrder(current_prio_expr)],
    }
    order_names, order = get_order(order_map, order_name)

    if not untracked:
        package_query = package_query.filter(Package.tracked == True)
    pkgs = package_query.filter(Package.blocked == False)\
                        .outerjoin(Package.last_build)\
                        .options(contains_eager(Package.last_build))\
                        .order_by(*order)

    page = pkgs.paginate(packages_per_page)
    for pkg, priority in page.items:
        pkg.current_priority = priority
    page.items = [pkg for pkg, _ in page.items]
    populate_package_groups(page.items)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, collection=collection,
                           **template_args)


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

    running_icon = property(package_running_icon)


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
    page.items = list(map(UnifiedPackage, page.items))
    populate_package_groups(page.items)
    return render_template(template, packages=page.items, page=page,
                           order=order_names, collection=None, **template_args)


# tab definitions
collection_tab = Tab('Collections', 0)
package_tab = Tab('Packages', 10)
group_tab = Tab('Groups', 20)
stats_tab = Tab('Stats', 100)
my_packages_tab = Tab('My packages', 30, requires_user=True)
add_packages_tab = Tab('Add packages', 50, requires_user=True)


@app.route('/collections')
@collection_tab.master
def collection_list():
    groups = db.query(CollectionGroup)\
        .options(joinedload(CollectionGroup.collections))\
        .order_by(CollectionGroup.name)\
        .all()
    categorized_ids = {
        collection.id for group in groups for collection in group.collections
    }
    uncategorized = [
        collection for collection in g.collections if collection.id not in categorized_ids
    ]
    return render_template("list-collections.html", groups=groups,
                           uncategorized=uncategorized)


@app.route('/packages')
@package_tab.master
def package_list():
    return package_view("list-packages.html")


@app.route('/')
@package_tab
def frontpage():
    return app.view_functions[frontend_config['frontpage']](
        **frontend_config['frontpage_kwargs']
    )


@app.route('/package/<name>')
@package_tab
def package_detail(name, form=None, collection=None):
    if not collection:
        collection = g.current_collections[0]

    g.current_collections = [collection]

    base = db.query(BasePackage).filter_by(name=name).first_or_404()
    packages = {p.collection_id: p for p in db.query(Package).filter_by(base_id=base.id)}

    # assign packages to collections in the right order, package may stay None
    package = None
    all_packages = []
    for coll in g.collections:
        p = packages.get(coll.id)
        if p:
            all_packages.append((coll, p))
            if coll is collection:
                package = p

    # prepare group checkboxes
    base.global_groups = db.query(PackageGroup)\
        .join(PackageGroupRelation)\
        .filter(PackageGroupRelation.base_id == base.id)\
        .filter(PackageGroup.namespace == None)\
        .all()
    base.user_groups = []
    base.available_groups = []
    if g.user:
        user_groups = \
            db.query(PackageGroup,
                     func.bool_or(PackageGroupRelation.base_id == base.id))\
            .outerjoin(PackageGroupRelation)\
            .join(GroupACL)\
            .filter(GroupACL.user_id == g.user.id)\
            .order_by(PackageGroup.namespace.nullsfirst(), PackageGroup.name)\
            .group_by(PackageGroup.id)\
            .distinct().all()
        base.user_groups = [group for group, checked in user_groups
                            if checked and group.namespace]
        base.available_groups = [group for group, checked in user_groups
                                 if not checked]

    # history entry pagination pivot id
    last_seen_ts = request.args.get('last_seen_ts')
    if last_seen_ts:
        try:
            last_seen_ts = int(last_seen_ts)
        except ValueError:
            abort(400)

    def to_ts(col):
        return cast(func.extract('EPOCH', col), Integer)

    entries = None

    if package:
        # set current priority
        package.current_priority = db.query(
            Package.current_priority_expression(
                collection=package.collection,
                last_build=package.last_build,
            )
        ).filter(Package.id == package.id).scalar()
        # prepare history entries - builds and resolution changes
        builds = db.query(Build)\
            .filter_by(package_id=package.id)\
            .filter(to_ts(Build.started) < last_seen_ts
                    if last_seen_ts else true())\
            .options(subqueryload(Build.dependency_changes),
                     subqueryload(Build.build_arch_tasks))\
            .order_by(Build.started.desc())\
            .limit(builds_per_page)\
            .all()
        resolutions = db.query(ResolutionChange)\
            .filter_by(package_id=package.id)\
            .filter(to_ts(ResolutionChange.timestamp) < last_seen_ts
                    if last_seen_ts else true())\
            .options(joinedload(ResolutionChange.problems))\
            .order_by(ResolutionChange.timestamp.desc())\
            .limit(builds_per_page)\
            .all()

        entries = sorted(
            builds + resolutions,
            key=lambda x: getattr(x, 'started', None) or getattr(x, 'timestamp'),
            reverse=True,
        )[:builds_per_page]

        if not form:
            form = forms.EditPackageForm(
                tracked=package.tracked,
                collection_id=package.collection_id,
                manual_priority=package.manual_priority,
                arch_override=(package.arch_override or '').split(' '),
                skip_resolution=package.skip_resolution,
            )

    # Note: package might be None
    return render_template(
        "package-detail.html",
        base=base,
        package=package,
        collection=collection,
        form=form,
        entries=entries,
        all_packages=all_packages,
        is_continuation=bool(last_seen_ts),
        is_last=len(entries) < builds_per_page if package else True,
    )


@app.route('/build/<int:build_id>')
@package_tab
def build_detail(build_id):
    # pylint: disable=E1101
    build = db.query(Build)\
              .options(joinedload(Build.package),
                       subqueryload(Build.dependency_changes),
                       subqueryload(Build.build_arch_tasks))\
              .filter_by(id=build_id).first_or_404()
    return render_template("build-detail.html", build=build,
                           cancel_form=forms.EmptyForm())


@app.route('/build/<int:build_id>/cancel', methods=['POST'])
@package_tab
@auth.login_required()
def cancel_build(build_id):
    if not g.user.admin:
        abort(403)
    build = db.query(Build).filter_by(id=build_id).first_or_404()
    if forms.EmptyForm().validate_or_flash():
        if build.state != Build.RUNNING:
            flash_nak("Only running builds can be canceled.")
        elif build.cancel_requested:
            flash_nak("Build already has pending cancelation request.")
        else:
            flash_ack("Cancelation request sent.")
            session.log_user_action(
                "Build (id={build.id}, task_id={build.task_id}) cancelation requested"
                .format(build=build)
            )
            build.cancel_requested = True
            db.commit()
    return redirect(url_for('package_detail', name=build.package.name))


@app.route('/groups')
@group_tab.master
def groups_overview():
    groups = db.query(PackageGroup)\
               .options(undefer(PackageGroup.package_count))\
               .filter_by(namespace=None)\
               .order_by(PackageGroup.name).all()
    return render_template("list-groups.html", groups=groups)


@app.route('/groups/<name>')
@app.route('/groups/<namespace>/<name>')
@group_tab
def group_detail(name=None, namespace=None):
    group = db.query(PackageGroup)\
              .filter_by(name=name, namespace=namespace).first_or_404()
    owners = ", ".join(owner.name for owner in group.owners)

    def query_fn(query):
        return query.outerjoin(PackageGroupRelation,
                               PackageGroupRelation.base_id == BasePackage.id)\
            .filter(PackageGroupRelation.group_id == group.id)

    return package_view("group-detail.html", query_fn=query_fn,
                        group=group, owners=owners)


@app.route('/user/<username>')
@package_tab
@my_packages_tab.master
@auth.login_required()
def user_packages(username):
    names = []
    try:
        results = plugin.dispatch_event('get_user_packages',
                                        session,
                                        username=username)
        for result in results:
            if result:
                names += result
    except Exception:
        flash_nak("Error retrieving user's packages")
        session.log.exception("Error retrieving user's packages")

    def query_fn(query):
        return query.filter(BasePackage.name.in_(names) if names else false())

    return package_view("user-packages.html", query_fn, username=username)


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
            form = forms.GroupForm(**obj)
        else:
            form = forms.GroupForm(owners=[g.user.name])
        return render_template('edit-group.html', group=group, form=form)
    form = forms.GroupForm()
    # check permissions
    if group and not group.editable:
        flash_nak("You don't have permission to edit this group")
        return redirect(url_for('group_detail', name=group.name,
                                namespace=group.namespace))
    # check form validity
    if not form.validate_or_flash():
        return render_template('edit-group.html', group=group, form=form)

    # existing group being edited or None - to be sent into template
    existing_group = group

    if not group:
        group = PackageGroup(namespace=g.user.name)
        db.add(group)
    group.name = form.name.data
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        flash_nak("Group already exists")
        return render_template('edit-group.html', group=existing_group, form=form)
    try:
        data.set_group_content(session, group, form.packages.data)
        data.set_group_maintainers(session, group, form.owners.data)
    except data.PackagesDontExist as e:
        db.rollback()
        flash_nak(str(e))
        return render_template('edit-group.html', group=existing_group, form=form)
    db.commit()
    flash_ack("Group created" if not existing_group else "Group modified")
    return redirect(url_for('group_detail', name=group.name,
                            namespace=group.namespace))


@app.route('/add-group', methods=['GET', 'POST'])
@group_tab
@auth.login_required()
def add_group():
    return process_group_form()


@app.route('/groups/<name>/edit', methods=['GET', 'POST'])
@app.route('/groups/<namespace>/<name>/edit', methods=['GET', 'POST'])
@group_tab
@auth.login_required()
def edit_group(name, namespace=None):
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(name=name, namespace=namespace).first_or_404()
    return process_group_form(group=group)


@app.route('/groups/<name>/delete', methods=['POST'])
@app.route('/groups/<namespace>/<name>/delete', methods=['POST'])
@auth.login_required()
def delete_group(name, namespace=None):
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(name=name, namespace=namespace).first_or_404()
    if not forms.EmptyForm().validate_or_flash() or not group.editable:
        abort(401)
    data.delete_group(session, group)
    db.commit()
    flash_ack("Group was deleted")
    return redirect(url_for('groups_overview'))


@app.route('/groups/<name>/delete', methods=['GET'])
@app.route('/groups/<namespace>/<name>/delete', methods=['GET'])
@auth.login_required()
def confirm_delete_group(name, namespace=None):
    group = db.query(PackageGroup)\
              .options(joinedload(PackageGroup.packages))\
              .filter_by(name=name, namespace=namespace).first_or_404()
    if not group.editable:
        abort(401)
    return render_template('delete-group.html', group=group,
                           form=forms.EmptyForm())


@app.route('/add-packages', methods=['GET', 'POST'])
@add_packages_tab.master
@auth.login_required()
def add_packages():
    form = forms.AddPackagesForm()
    if request.method == 'POST':
        if not form.validate_or_flash():
            return render_template("add-packages.html", form=form)
        names = set(form.packages.data)
        try:
            collection = [c for c in g.collections
                          if c.name == form.collection.data][0]
        except IndexError:
            abort(404)

        try:
            added = data.track_packages(session, collection, names)
        except data.PackagesDontExist as e:
            db.rollback()
            flash_nak(str(e))
            return render_template("add-packages.html", form=form)

        if form.group.data:
            namespace, name = PackageGroup.parse_name(form.group.data)
            group = db.query(PackageGroup)\
                      .filter_by(namespace=namespace, name=name)\
                      .first_or_404()
            if not group.editable:
                abort(400)
            data.set_group_content(session, group, names, append=True)

        flash_ack("Packages added: {}".format(','.join(p.name for p in added)))
        db.commit()
        return redirect(request.form.get('next') or url_for('frontpage'))
    return render_template("add-packages.html", form=form)


@app.route('/documentation')
def documentation():
    return render_template("documentation.html")


@app.route('/search')
@package_tab
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
    form = forms.EditPackageForm()
    collection = g.collections_by_id.get(form.collection_id.data) or abort(400)
    if not form.validate_or_flash():
        return package_detail(name=name, form=form, collection=collection)
    package = db.query(Package)\
        .filter_by(name=name, collection_id=collection.id)\
        .first_or_404()

    for key, prev_val in request.form.items():
        if key.startswith('group-prev-'):
            group = db.query(PackageGroup).get_or_404(int(key[len('group-prev-'):]))
            new_val = request.form.get('group-{}'.format(group.id))
            if bool(new_val) != (prev_val == 'true'):
                if not group.editable:
                    abort(403)
                if new_val:
                    data.set_group_content(session, group, [package.name], append=True)
                else:
                    data.set_group_content(session, group, [package.name], delete=True)

    if form.tracked.data is not None:
        data.set_package_attribute(
            session, package, 'tracked',
            form.tracked.data,
        )
    if form.manual_priority.data is not None:
        data.set_package_attribute(
            session, package, 'manual_priority',
            form.manual_priority.data,
        )
    if form.arch_override.data is not None:
        data.set_package_attribute(
            session, package, 'arch_override',
            ' '.join(form.arch_override.data) or None,
        )
    if form.skip_resolution.data is not None:
        data.set_package_attribute(
            session, package, 'skip_resolution',
            form.skip_resolution.data,
        )
        if package.skip_resolution:
            package.resolved = None
            db.query(UnappliedChange).filter_by(package_id=package.id).delete()
    flash_ack("Package modified")

    db.commit()
    return redirect(url_for('package_detail', name=package.name) +
                    "?collection=" + collection.name)


@app.route('/bugreport/<name>')
def bugreport(name):
    package = db.query(Package)\
                .filter(Package.name == name)\
                .filter(Package.blocked == False)\
                .filter(Package.last_complete_build_id != None)\
                .filter(Package.collection_id == g.current_collections[0].id)\
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
    query = urllib.parse.urlencode(bug)
    bugreport_url = get_config('bugreport.url').format(query=query)
    return redirect(bugreport_url)


@app.route('/collection/<name>')
@collection_tab
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


@app.route('/affected-by/<dep_name>')
def affected_by(dep_name):
    if len(g.current_collections) != 1:
        abort(400)
    collection = g.current_collections[0]
    try:
        evr1 = RpmEVR(
            int(request.args['epoch1']),
            request.args['version1'],
            request.args['release1']
        )
        evr2 = RpmEVR(
            int(request.args['epoch2']),
            request.args['version2'],
            request.args['release2']
        )
    except (KeyError, ValueError):
        abort(400)

    deps_in = (
        db.query(Dependency.id)
        .filter(Dependency.name == dep_name)
        .filter(Dependency.evr > evr1)
        .filter(Dependency.evr < evr2)
        .cte('deps_in')
    )
    deps_higher = (
        db.query(Dependency.id)
        .filter(Dependency.name == dep_name)
        .filter(Dependency.evr >= evr2)
        .cte('deps_higher')
    )
    deps_lower = (
        db.query(Dependency.id)
        .filter(Dependency.name == dep_name)
        .filter(Dependency.evr <= evr1)
        .cte('deps_lower')
    )
    filtered_changes = union(
        db.query(AppliedChange)
        .filter(AppliedChange.prev_dep_id.in_(db.query(deps_in))),
        db.query(AppliedChange)
        .filter(AppliedChange.curr_dep_id.in_(db.query(deps_in))),
        db.query(AppliedChange)
        .filter(
            (AppliedChange.prev_dep_id.in_(db.query(deps_lower))) &
            (AppliedChange.curr_dep_id.in_(db.query(deps_higher)))
        ),
    ).alias('filtered_changes')
    prev_build = aliased(Build)
    subq = db.query(prev_build.state.label('prev_state'))\
        .order_by(prev_build.started.desc())\
        .filter(prev_build.started < Build.started)\
        .filter(prev_build.package_id == Build.package_id)\
        .limit(1)\
        .correlate().as_scalar()
    prev_dep = aliased(Dependency)
    curr_dep = aliased(Dependency)
    failed = (
        db.query(
            prev_dep.name.label('dep_name'),
            prev_dep.evr.label('prev_evr'),
            curr_dep.evr.label('curr_evr'),
            AppliedChange.distance,
            Build.id.label('build_id'),
            Build.state.label('build_state'),
            Build.started.label('build_started'),
            Package.name.label('package_name'),
            Package.resolved.label('package_resolved'),
            Package.last_complete_build_state.label('package_lb_state'),
            subq.label('prev_build_state'),
        )
        .select_entity_from(filtered_changes)
        .join(prev_dep, AppliedChange.prev_dep)
        .join(curr_dep, AppliedChange.curr_dep)
        .join(AppliedChange.build).join(Build.package)
        .filter_by(blocked=False, tracked=True, collection_id=collection.id)
        .filter(Build.state == Build.FAILED)
        .filter(subq != Build.FAILED)
        .order_by(AppliedChange.distance, Build.started.desc())
        .all()
    )

    def package_state(row):
        return Package(
            tracked=True,
            blocked=False,
            resolved=row.package_resolved,
            last_complete_build_state=row.package_lb_state,
        ).state_string

    return render_template("affected-by.html", package_state=package_state,
                           dep_name=dep_name, evr1=evr1, evr2=evr2,
                           collection=collection, failed=failed)


@app.route('/stats')
@stats_tab.master
def statistics():
    now = db.query(func.now()).scalar()
    scalar_stats = db.query(ScalarStats).one()
    resource_query = db.query(ResourceConsumptionStats)\
        .order_by(ResourceConsumptionStats.time.desc())\
        .paginate(20)
    return render_template("stats.html", now=now, stats=scalar_stats,
                           packages=resource_query.items,
                           page=resource_query)


@app.route('/badge/<collection>/<name>.svg')
@app.route('/badge/<collection>/<name>.png')
def badge(name, collection):
    c = g.collections_by_name.get(collection) or abort(404, "Collection not found")
    p = db.query(Package).filter_by(name=name, collection_id=c.id).first_or_404()
    image = 'images/badges/{}.{}'.format(p.state_string, request.path[-3:])
    return redirect(url_for('static', filename=image))
