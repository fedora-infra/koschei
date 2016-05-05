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


if [ ! -f $PWD/koschei/models.py ]; then
    echo "This script must be sourced from Koschei root directory" >&2
    return 1
fi

export PGHOST=$PWD/test/db
export PGDATA=$PWD/test/db

pg_init()
{
    pg_ctl -s -w init -o "-A trust"
}

pg_start()
{
    pg_ctl -s -w start -o "-F -h '' -k $PGDATA"
}

pg_stop()
{
    pg_ctl -s -w stop -m immediate
}

pg_restart()
{
    pg_ctl -s -w restart -m immediate -o "-F -h '' -k $PGDATA"
}

pg_reload()
{
    pg_ctl -s -w reload
}

pg_status()
{
    pg_ctl -s -w status
}
