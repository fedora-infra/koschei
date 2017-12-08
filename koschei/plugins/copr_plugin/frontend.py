# Copyright (C) 2016 Red Hat, Inc.
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

from __future__ import print_function, absolute_import

from flask import abort, render_template, url_for, redirect, g
from sqlalchemy import func
from sqlalchemy.orm import joinedload, subqueryload
from wtforms import validators, widgets, IntegerField, StringField

from koschei.config import get_config
from koschei.frontend import auth
from koschei.frontend.base import app, db
from koschei.frontend.forms import StrippedStringField, EmptyForm
from koschei.frontend.tabs import Tab
from koschei.frontend.util import flash_ack
from koschei.models import User, CoprRebuildRequest, CoprRebuild

app.jinja_env.globals.update(
    copr_frontend_url=get_config('copr.frontend_url'),
    copr_owner=get_config('copr.copr_owner'),
    copr_chroot_name=get_config('copr.chroot_name'),
    build_log_url=get_config('copr.build_log_url'),
)


def can_create_request():
    return g.user and (not get_config('copr.require_admin') or g.user.admin)


def can_edit_request(copr_request):
    return g.user and (copr_request.user_id == g.user.id or g.user.admin)


CoprRebuildRequest.editable = property(can_edit_request)


class RebuildRequestForm(EmptyForm):
    collection = StrippedStringField('collection')
    copr_name = StrippedStringField('copr_name', [validators.Length(min=1)])
    description = StrippedStringField('description', widget=widgets.TextArea())
    schedule_count = IntegerField('schedule_count', [validators.NumberRange(min=0)],
                                  default=get_config('copr.default_schedule_count'))


class EditRebuildForm(EmptyForm):
    request_id = IntegerField('request_id')
    package_id = IntegerField('package_id')
    action = StringField('action', [validators.AnyOf(['move-top', 'remove'])])


user_rebuilds_tab = Tab('My user rebuilds', 60, requires_user=True)


@app.route('/rebuild-request/user/<username>')
@user_rebuilds_tab.master
def list_rebuild_requests(username):
    user = db.query(User).filter_by(name=username).first_or_404()
    requests = db.query(CoprRebuildRequest)\
        .filter(CoprRebuildRequest.user_id == user.id)\
        .order_by(CoprRebuildRequest.id.desc())\
        .all()
    form = RebuildRequestForm() if can_create_request() and user == g.user else None
    return render_template('list-rebuild-requests.html',
                           user=user, requests=requests, form=form)


@app.route('/rebuild-request/new', methods=['POST'])
@auth.login_required()
@user_rebuilds_tab
def new_rebuild_request():
    if get_config('copr.require_admin') and not g.user.admin:
        abort(403)
    form = RebuildRequestForm()
    if form.validate_or_flash():
        collection = g.collections_by_name.get(form.collection.data)
        if not collection:
            abort(404, "Collection not found")
        repo_source = form.copr_name.data
        if '/' not in repo_source:
            repo_source = g.user.name + '/' + repo_source
        repo_source = 'copr:' + repo_source
        rebuild_request = CoprRebuildRequest(
            collection_id=collection.id,
            user_id=g.user.id,
            repo_source=repo_source,
            description=form.description.data or None,
            schedule_count=form.schedule_count.data,
        )
        db.add(rebuild_request)
        db.commit()
        flash_ack('Rebuild request created')
        return redirect(url_for('rebuild_request_detail',
                                request_id=rebuild_request.id))
    return redirect(url_for('list_rebuild_requests', username=g.user.name))


@app.route('/rebuild-request/<int:request_id>')
@user_rebuilds_tab
def rebuild_request_detail(request_id):
    rebuild_request = db.query(CoprRebuildRequest).options(
        subqueryload('resolution_changes'),
        joinedload('resolution_changes.package'),
        subqueryload('rebuilds'),
        joinedload('rebuilds.package'),
    ).get_or_404(request_id)
    return render_template('rebuild-request-detail.html',
                           request=rebuild_request,
                           form=EditRebuildForm())


@app.route('/rebuild-request/edit-rebuild', methods=['POST'])
@auth.login_required()
def edit_rebuild():
    form = EditRebuildForm()
    if not form.validate_on_submit():
        abort(400)
    rebuild = db.query(CoprRebuild)\
        .filter_by(request_id=form.request_id.data,
                   package_id=form.package_id.data)\
        .first_or_404()
    if not rebuild.request.editable:
        abort(403)
    if form.action.data == 'move-top':
        db.query(CoprRebuild)\
            .filter(CoprRebuild.request_id == rebuild.request_id)\
            .filter(CoprRebuild.state == None)\
            .filter(CoprRebuild.order < rebuild.order)\
            .update({'order': CoprRebuild.order + 1})
        rebuild.order = db.query(func.min(CoprRebuild.order) - 1)\
            .filter(CoprRebuild.request_id == rebuild.request_id)\
            .filter(CoprRebuild.state == None)\
            .scalar()
        # Moving to top should ensure the package will be scheduled
        rebuild.request.schedule_count += 1
        rebuild.request.state = 'in progress'
    elif form.action.data == 'remove':
        db.query(CoprRebuild)\
            .filter(CoprRebuild.request_id == rebuild.request_id)\
            .filter(CoprRebuild.order > rebuild.order)\
            .update({'order': CoprRebuild.order - 1})
        db.delete(rebuild)
    db.commit()
    return redirect(url_for('rebuild_request_detail', request_id=rebuild.request_id))
