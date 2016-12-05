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

import re

from flask import flash
from flask_wtf import Form

from wtforms import StringField, TextAreaField
from wtforms.validators import Regexp, ValidationError


class StrippedStringField(StringField):
    def process_formdata(self, values):
        # pylint:disable=W0201
        self.data = values and values[0].strip()


class ListFieldMixin(object):
    split_re = re.compile(r'[ \t\n\r,]+')

    def process_formdata(self, values):
        # pylint:disable=W0201
        values = values and values[0]
        self.data = [x for x in self.split_re.split(values or '') if x]


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
