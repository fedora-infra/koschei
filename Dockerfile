FROM registry.fedoraproject.org/fedora:35
ENV PYTHONPATH=/usr/share/koschei
EXPOSE 8080

RUN : \
 && dnf -y --refresh update \
 && dnf -y install \
      python3-sqlalchemy \
      python3-psycopg2 \
      python3-rpm \
      python3-flask \
      python3-flask-sqlalchemy \
      python3-flask-wtf \
      python3-wtforms \
      python3-humanize \
      python3-jinja2 \
      python3-markupsafe \
      python3-memcached \
      python3-mod_wsgi \
      python3-fedora-messaging \
      httpd \
      js-jquery \
      mod_auth_openidc \
      python3-koji \
      python3-hawkey \
      python3-librepo \
      python3-dogpile-cache \
      python3-alembic \
      postgresql \
 && dnf -y clean all \
 && useradd koschei \
 && :

COPY bin/ /usr/bin/
COPY ./ /usr/share/koschei/

RUN : \
 && sed 's|@CACHEDIR@|/var/cache/koschei|g; s|@DATADIR@|/usr/share/koschei|g; s|@CONFDIR@|/etc/koschei|g; s|@STATEDIR@|/var/lib/koschei|g' /usr/share/koschei/config.cfg.template >/usr/share/koschei/config.cfg \
 && sed -i s/@VERSION@/$(sed 's/\(.......\).*/\1/' /usr/share/koschei/.git/$(cat /usr/share/koschei/.git/HEAD | sed 's/.*: *//'))/ /usr/share/koschei/config.cfg \
 && chmod -R a+rwX /usr/share/koschei/ \
 && mkdir -m 777 /var/cache/koschei/ /var/cache/koschei/repodata/ \
 && :

USER koschei
