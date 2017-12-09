FROM fedora:27

WORKDIR /build

RUN echo -e "deltarpm=0\ninstall_weak_deps=0\ntsflags=nodocs" >> /etc/dnf/dnf.conf
RUN dnf -y update
RUN dnf -y install 'dnf-command(builddep)' python3-nose python3-coverage curl findutils

COPY koschei.spec .
RUN dnf -y builddep koschei.spec

RUN useradd koschei

COPY . .

RUN chown koschei . test
RUN chown -R koschei test/repos

USER koschei

CMD ["/build/.travis/run.sh"]
