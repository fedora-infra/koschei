%bcond_without tests

Name:           koschei
Version:        0.0.1
Release:        1%{?dist}
Summary:        Continuous integration for Fedora packages
License:        GPLv2+
URL:            https://github.com/msimacek/koschei
Source0:        %{name}-%{version}.tar.xz
BuildArch:      noarch

BuildRequires:  python-devel
BuildRequires:  systemd

%if %{with tests}
BuildRequires:       python-sqlalchemy
BuildRequires:       koji
BuildRequires:       python-hawkey
BuildRequires:       python-librepo
BuildRequires:       python-libcomps
BuildRequires:       rpm-python
%endif

Requires:       python-sqlalchemy
Requires:       koji
Requires:       fedmsg
Requires:       postgresql-server
Requires:       python-psycopg2
Requires:       createrepo_c
Requires:       curl
Requires:       python-jinja2
Requires:       python-hawkey
Requires:       python-alembic
Requires:       python-flask
Requires:       python-flask-sqlalchemy
Requires:       mod_wsgi
Requires:       httpd
Requires:       python-librepo
Requires:       python-libcomps
Requires:       rpm-python
Requires(pre):  shadow-utils
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

%description
Service tracking dependency changes in Fedora and rebuilding packages whose
dependencies change too much. It uses Koji scratch builds to do the rebuilds and
provides a web interface to the results.

%prep
%setup -q -c -n %{name}

sed 's|@CACHEDIR@|%{_localstatedir}/cache/%{name}|g
     s|@DATADIR@|%{_datadir}/%{name}|g' config.cfg.template > config.cfg

%build
%{__python} setup.py build

%install
%{__python} setup.py install --skip-build --root %{buildroot}

mkdir -p %{buildroot}%{_sysconfdir}/%{name}
cp -p config.cfg %{buildroot}%{_sysconfdir}/%{name}/

install -dm 755 %{buildroot}%{_unitdir}
for unit in systemd/*; do
    install -pm 644 $unit %{buildroot}%{_unitdir}/
done

mkdir -p %{buildroot}%{_bindir}
install -pm 755 admin.py %{buildroot}%{_bindir}/koschei-admin

install -dm 755 %{buildroot}%{_localstatedir}/cache/%{name}/repodata
install -dm 755 %{buildroot}%{_localstatedir}/cache/%{name}/srpms

mkdir -p %{buildroot}%{_datadir}/%{name}
cp -pr templates %{buildroot}%{_datadir}/%{name}/

cp -pr alembic/ alembic.ini %{buildroot}%{_datadir}/%{name}/
cp -pr theme %{buildroot}%{_datadir}/%{name}/
ln -s theme/fedora/static %{buildroot}%{_datadir}/%{name}/static
cp -p %{name}.wsgi %{buildroot}%{_datadir}/%{name}/
mkdir -p %{buildroot}%{_sysconfdir}/httpd/conf.d
cp -p httpd.conf %{buildroot}%{_sysconfdir}/httpd/conf.d/%{name}.conf

%if %{with tests}
%check
%{__python} setup.py test
%endif

%pre
getent group %{name} >/dev/null || groupadd -r %{name}
getent passwd %{name} >/dev/null || \
    useradd -r -g %{name} -d %{_datadir}/%{name} -s /bin/sh \
    -c "Runs koschei services" %{name}
exit 0

%post
%systemd_post koschei-scheduler.service
%systemd_post koschei-watcher.service
%systemd_post koschei-polling.service
%systemd_post koschei-resolver.service

%preun
%systemd_preun koschei-scheduler.service
%systemd_preun koschei-watcher.service
%systemd_preun koschei-polling.service
%systemd_preun koschei-resolver.service

%postun
%systemd_postun_with_restart koschei-scheduler.service
%systemd_postun_with_restart koschei-watcher.service
%systemd_postun_with_restart koschei-polling.service
%systemd_postun_with_restart koschei-resolver.service

%files
%doc LICENSE.txt
%{_bindir}/koschei-admin
%{_datadir}/%{name}
%attr(755, %{name}, %{name}) %{_localstatedir}/cache/%{name}
%{python_sitelib}/*
%dir %{_sysconfdir}/%{name}
%config(noreplace) %{_sysconfdir}/%{name}/config.cfg
%config %{_sysconfdir}/httpd/conf.d/%{name}.conf
%{_unitdir}/*

%changelog
* Fri Jun 13 2014 Michael Simacek <msimacek@redhat.com> - 0.0.1-1
- Initial version
