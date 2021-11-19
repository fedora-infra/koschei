FROM registry.fedoraproject.org/fedora:33

WORKDIR /build

RUN echo -e "deltarpm=0\ninstall_weak_deps=0\ntsflags=nodocs" >> /etc/dnf/dnf.conf
RUN dnf -y update
RUN dnf -y install curl findutils postgresql-server python3-coverage python3-devel python3-dogpile-cache python3-fedora-messaging python3-flask python3-flask-sqlalchemy python3-flask-wtf python3-hawkey python3-humanize python3-jinja2 python3-markupsafe python3-koji python3-librepo python3-mock python3-nose python3-psycopg2 python3-requests python3-rpm python3-setuptools python3-sqlalchemy python3-vcrpy python3-wtforms systemd

RUN useradd koschei

COPY . .

RUN chown koschei . test
RUN chown -R koschei test/repos

USER koschei

CMD ["/build/.travis/run.sh"]
