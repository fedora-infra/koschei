[![Build Status](https://travis-ci.org/fedora-infra/koschei.svg?branch=master)](https://travis-ci.org/fedora-infra/koschei)
[![test coverage](https://img.shields.io/codecov/c/github/fedora-infra/koschei/master.svg)](https://codecov.io/gh/fedora-infra/koschei)



Koschei
=======

Koschei is a software for running a service for scratch-rebuilding RPM
packages in Koji instance when their build-dependencies change or
after some time elapse.


Dependencies
------------

Python code dependencies:
- alembic
- fedora_messaging
- flask
- flask-sqlalchemy
- hawkey
- humanize
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
- fedora-messaging (optional)


Development
-----------
Koschei git repository includes a Vagrantfile, which can be used to provision
a VM with the following:
- initialized database with the copy of contents from Fedora production server
- the source attached at `/vagrant`
- everything necessary symlinked to be able to run admin script, backend
  services and frontend out-of-the-box
- httpd running with the frontend on port 5000 on your machine

How to use it:
- Install vagrant: `dnf install vagrant-libvirt vagrant-sshfs ansible`
- Run `vagrant up`
- Frontend is already running on port 80, mapped to port 5000 on your local
  machine
- The admin script can be run as `koschei-admin` (it's symlink to the actual
  source file in `/vagrant`)
- Backend services can be run with `python -m koschei.backend.main
  service_name`. If you need to run services that need a Koji certificate,
  you'll need to scp your certificates into `/etc/koschei` in the machine.
- There is a helper script `koschei-ipython` that drops you into ipython shell
  with koschei backend initialized and database and koji sessions bound to
  variables `db` and `k`, respectively. This can be useful for development since
  you don't need to run full service to test a particular function or code
  snippet


Configuration
-------------
The configuration is formed by merging default configuration values and the
local configuration in `/etc/koschei/`. The backend, frontend and admin script
have separate configuration files in `/etc/koschei`, named `config-backend.cfg`,
`config-frontend.cfg` and `config-admin.cfg`, respectively. The cfg files are
regular Python files that expect assignment to `config` dictionary variable. The
default configuration file is stored at `/usr/share/koschei/config.cfg` and
contains comments documenting possible configuration options. Keep in mind that
the merging of configurations is recursive, it merges all dictionaries, not
just the top-level ones.


Deployment
----------
For production deployment use Koschei container images.

Koschei is split into multiple components that can function
independently - backend, frontend and admin.  Each are configured
separately and can be deployed in different containers on different
machines.

Setting up the database:
- Install PostgreSQL server with `dnf install postgresql-server`. Other
  database servers are not supported and won't work.
- Execute `postgresql-setup initdb` to initialize the database
- Enable the service with `systemctl enable postgresql-server`
  and start it with `systemctl start postgresq-server`
- Create the database with `createdb koschei`
- If your database is on separate host or you didn't follow the steps here
  exactly, you'll need to configure the database connection in respective
  configuration files of backend, frontend and admin (see configuration section).
- Populate DB schema with `koschei-admin create-db`
- Create at least one package collection using `koschei-admin create-collection`
  (see its help for parameters)


Koschei administration script `koschei-admin` is independent from
other services.  It is used to perform various administration tasks
such as adding packages or creating collections. See its help (`-h`
option) for list of commands and help of individual commands (such as
`koschei-admin create-collection -h`).

Koschei backend consists of multiple services that can be ran
separately, for example in different containers.
For fully working instance you'll want to run all of them, for passive
instance that doesn't submit builds, you'll want to skip koschei-scheduler.
For submiting builds, you need to install a koji certificate at
`/home/koschei/.fedora.cert` (and also the CA and server CA certificates). The
cert files have the same layout as when generated using fedora-cert and using
fedpkg or koji client. If you want to use different locations, you can specify
them in the `config-backend.cfg` file.

The web frontend is a WSGi application, which can be run within Apache
httpd server.  Koschei ships httpd configuration file that should work
out-of-the-box as you start httpd. You should override the application
secret used for authentication in `/etc/koschei/config-frontend.cfg`.


Updating
--------
After Koschei update to a newer version, you need to manually stop the
services (including httpd) and execute DB migrations. Migrations are
executed by `koschei-admin alembic upgrade head`.  Then the services
can be started again.


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
