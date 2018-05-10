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

"""
This module defines all non-plugin Flask-WTF-based forms used by frontend.
"""

import re

from flask_wtf import Form
from wtforms import (
    StringField, TextAreaField, IntegerField, BooleanField,
)
from wtforms.validators import Regexp, ValidationError
from wtforms.widgets import HTMLString, HiddenInput

from koschei.config import get_koji_config
from koschei.frontend.util import flash_nak


class CheckBoxField(BooleanField):
    """
    Check box field that contains an additional hidden field that esures that
    the value is not set to False when the checkbox was not present at all
    """
    # pylint: disable=arguments-differ,attribute-defined-outside-init
    def process(self, formdata, *args, **kwargs):
        super(CheckBoxField, self).process(formdata, *args, **kwargs)
        if formdata and not formdata.get(self.name + '__present', None):
            self.data = None

    def __call__(self, **kwargs):
        marker = '<input type="hidden" name="{name}__present" value="1"/>'\
            .format(name=self.name)
        return HTMLString(self.meta.render_field(self, kwargs) + marker)


class StrippedStringField(StringField):
    """
    String field that automatically strips whitespace.
    """
    # pylint:disable=arguments-differ
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
    """
    Text field of comma- or whitespace-separated entries.
    """
    def _value(self):
        return ', '.join(self.data or ())


class ListAreaField(ListFieldMixin, TextAreaField):
    """
    TextArea field of comma- or whitespace-separated entries.
    """
    def _value(self):
        return '\n'.join(self.data or ())


name_re = re.compile(r'^[a-zA-Z0-9.+_-]+$')
group_re = re.compile(r'^([a-zA-Z0-9.+_-]+(/[a-zA-Z0-9.+_-]+)?)?$')


class NameListValidator(object):
    """
    Validator for a list of package names. Used for user names as well.
    """
    def __init__(self, message):
        self.message = message

    def __call__(self, _, field):
        if not all(map(name_re.match, field.data)):
            raise ValidationError(self.message)


arch_override_re = re.compile(r'\^?(.*)')


class ArchOverrideValidator(object):
    """
    Validates arch overide field format.
    The field must be a list.
    The allowed entries are specified by the configuration. They may have a caret prefixed
    to signify a set complement.
    """
    def __call__(self, _, field):
        allowed = get_koji_config('primary', 'build_arches')
        for arch in field.data:
            match = arch_override_re.match(arch)
            if not match or match.group(1) not in allowed:
                raise ValidationError("Unrecognized arch in arch_override")


class NonEmptyList(object):
    """
    List field validator that requires at least one item.
    """
    def __init__(self, message):
        self.message = message

    def __call__(self, _, field):
        if not field.data:
            raise ValidationError(self.message)


class EmptyForm(Form):
    """
    Base of all our forms. Can be used on its own as an empty form that performs CSRF
    validation on submit (for things like cancel or delete buttons).
    """
    def validate_or_flash(self):
        """
        If the form is valid, returns True.
        Otherwise sets appropriate validations errors as flash messages and returns False.
        """
        if self.validate_on_submit():
            return True
        flash_nak("Validation errors: " +
                  ', '.join(x for i in self.errors.values() for x in i))
        return False


class GroupForm(EmptyForm):
    """
    Form for PackageGroup creation/editing
    """
    name = StrippedStringField('name', [Regexp(name_re, message="Invalid group name")])
    packages = ListAreaField('packages', [NonEmptyList("Empty group not allowed"),
                                          NameListValidator("Invalid package list")])
    owners = ListField('owners', [NonEmptyList("Group must have an owner"),
                                  NameListValidator("Invalid owner list")])


class AddPackagesForm(EmptyForm):
    """
    Form for "Add package" view.
    """
    packages = ListAreaField('packages', [NonEmptyList("No packages given"),
                                          NameListValidator("Invalid package list")])
    collection = StrippedStringField('collection')
    group = StrippedStringField('group', [Regexp(group_re, message="Invalid group")])


class EditPackageForm(EmptyForm):
    """
    Form for "Package detail" view which allows editing package's properties and group
    membership.
    """
    tracked = CheckBoxField('tracked')
    collection_id = IntegerField(
        'collection_id',
        widget=HiddenInput(),
    )
    manual_priority = IntegerField('manual_priority')
    arch_override = ListField('arch_override', [ArchOverrideValidator()])
    skip_resolution = CheckBoxField('skip_resolution')
    # groups' checkboxes are processed manually
