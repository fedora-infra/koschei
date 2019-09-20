#!/bin/make -f

VERSION = $(shell git describe --tags)

all: config.cfg koschei-base koschei

config.cfg: config.cfg.template
	sed 's|@CACHEDIR@|/var/cache/koschei|g; s|@DATADIR@|/usr/share/koschei|g; s|@VERSION@|$(VERSION)|g; s|@CONFDIR@|/etc/koschei|g; s|@STATEDIR@|/var/lib/koschei|g' config.cfg.template >$@

koschei-base:
	@set -eux
	x=$$(buildah from registry.fedoraproject.org/fedora:29)
	buildah run $$x -- dnf -y --refresh install python3-sqlalchemy python3-psycopg2 python3-rpm python3-flask python3-flask-sqlalchemy python3-flask-wtf python3-wtforms python3-humanize python3-jinja2 python3-memcached python3-mod_wsgi python3-fedora-messaging httpd js-jquery mod_auth_openidc python3-koji python3-hawkey python3-librepo python3-dogpile-cache python3-alembic postgresql
	buildah run $$x -- useradd koschei
	buildah config --env PYTHONPATH=/usr/share/koschei $$x
	buildah commit --rm $$x $@

koschei:
	@set -eux
	x=$$(buildah from koschei-base)
	buildah copy --chown koschei:koschei $$x ./ /usr/share/koschei/
	buildah copy $$x bin/ /usr/bin/
	buildah run $$x -- chmod -R a+rwX /usr/share/koschei/
	buildah run $$x -- mkdir -m 777 /var/cache/koschei/ /var/cache/koschei/repodata/
	buildah config --port 8080 $$x
	buildah config --user koschei $$x
	buildah commit --rm $$x $@

# podman login quay.io/koschei/koschei
upload:
	@set -eux
	skopeo copy containers-storage:localhost/koschei:latest docker://quay.io/koschei/koschei:latest

.ONESHELL:
.PHONY: all koschei koschei-base upload
