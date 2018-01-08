#!/usr/bin/python3
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
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>

import sys
import os
import re
import mock

sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.abspath(__file__)), '..'))
with mock.patch('koschei.config.load_config'):
    import admin


print('''
_koschei_admin() {
  local i
  for (( i=1; i < COMP_CWORD; i++ )); do
    if [[ "${COMP_WORDS[i]}" != -* ]]; then
      case "${COMP_WORDS[i]}" in
''')

cmd_names = ['-h', '--help']
for Cmd in admin.Command.__subclasses__():
    cmd_args = ['-h', '--help']
    cmd_name = re.sub(r'([A-Z])', lambda s: '-' + s.group(0).lower(),
                      Cmd.__name__)[1:]
    cmd_names.append(cmd_name)
    class Parser(object):
        def add_argument(self, *args, **kwargs):
            if args[0][0] == '-':
                cmd_args.extend(args)
    Cmd().setup_parser(Parser())
    print('%s) COMPREPLY=($(compgen -W "%s" -- '
          '"${COMP_WORDS[COMP_CWORD]}"));;' % (
              cmd_name, " ".join(cmd_args)))

print('''
        *) COMPREPLY=();;
      esac
      return
    fi
    done
  COMPREPLY=($(compgen -W "%s" -- "${COMP_WORDS[COMP_CWORD]}"))
} &&
complete -F _koschei_admin koschei-admin
''' % ' '.join(cmd_names))
