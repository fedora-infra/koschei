Koschei
=======

Koschei is a software for running a service for scratch-rebuilding RPM
packages in Koji instance when their build-dependencies change or
after some time elapse.


Dependencies
------------

Python code dependencies:
- alembic
- fedmsg
- flask
- flask-openid
- flask-sqlalchemy
- hawkey
- jinja2
- koji
- librepo
- psycopg2
- rpm
- sqlalchemy

Test dependencies (optional):
- nose
- mock

Infrastructure:
- httpd with mod_wsgi (other WSGI servers should work too, but were not tested)
- postgresql (can be external service)
- koji hub (can be external service)
- fedmsg (optional)
- systemd (optional)


Deployment
----------
For production deployment install koschei RPM package.
Development snapshots are available at
https://msimacek.fedorapeople.org/koschei/repo/.

Setting up the database:
- Install PostgreSQL server with `yum install postgresql-server`. Other
  database servers are not supported and won't work.
- Execute `postgresql-setup --initdb`
- Enable the service with `systemctl enable postgresql-server`
  and start it with `systemctl start postgresq-server`
- Create the database with `createdb koschei`
- In case your DB instance is on another machine or uses different
  authentication method than the default `ident`, you'll need to configure the
  connection in koschei configuration file. See below.
- Populate DB schema with `koschei-admin createdb`

Koschei consists of multiple systemd services that can be started separately.
For fully working instance you'll want to start all of them, for passive
instance that doesn't submit builds, you'll want to skip koschei-scheduler.
For submiting builds, you need to install a koji certificate at
/home/koschei/.fedora.cert (and also the CA and server CA certificates). The
cert files have the same layout as when generated using fedora-cert and using
fedpkg or koji client.

The web interface is a WSGi application, which can be run within Apache Server.
The koschei RPM package ships httpd configuration file that should work
out-of-the-box as you start httpd.


Updating
--------
After a koschei package update to a newer version, you need to manually stop
the services (including httpd) and execute DB migrations. Migrations are
executed by `alembic -c /usr/share/koschei/alembic.ini upgrade head`. Then the
services can be started again.


Configuration
-------------
The configuration is formed by merging default configuration values and the
local configuration in /etc/koschei/config.cfg. The cfg files are regular
Python files that expect assignment to config dictionary variable. The default
config is stored at /usr/share/koschei/config.cfg and can serve as
a documentation of which values are possible. Keep in mind that the merging of
configurations is recursive, it merges all dictionaries, not just the top-level
ones.


Copying
-------

Koschei is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Koschei is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

A copy of the GNU General Public License is contained in the
LICENSE.txt file.
