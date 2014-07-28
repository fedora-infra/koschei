Name:           koschei
Version:        0.0.1
Release:        1%{?dist}
Summary:        Continuous integration for Fedora packages
License:        GPLv2+
URL:            TBD
Source0:        %{name}-%{version}.tar.xz
BuildArch:      noarch

BuildRequires:  python-devel
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
Requires:       mod_wsgi

%description
TBD.

%prep
%setup -q -c -n %{name}

sed 's|@CACHEDIR@|%{_localstatedir}/cache/%{name}|g
     s|@OUTPUTDIR@|%{_localstatedir}/www|g
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

%post
%systemd_post koschei-scheduler.service
%systemd_post koschei-submitter.service
%systemd_post koschei-watcher.service
%systemd_post koschei-reporter.service
%systemd_post koschei-log-dowloader.service
%systemd_post koschei-polling.service

%preun
%systemd_preun koschei-scheduler.service
%systemd_preun koschei-submitter.service
%systemd_preun koschei-watcher.service
%systemd_preun koschei-reporter.service
%systemd_preun koschei-log-dowloader.service
%systemd_preun koschei-polling.service

%postun
%systemd_postun_with_restart koschei-scheduler.service
%systemd_postun_with_restart koschei-submitter.service
%systemd_postun_with_restart koschei-watcher.service
%systemd_postun_with_restart koschei-reporter.service
%systemd_postun_with_restart koschei-log-dowloader.service
%systemd_postun_with_restart koschei-polling.service

%files
%doc LICENSE.txt
%{_bindir}/koschei-admin
%{_datadir}/%{name}
%{_localstatedir}/cache/%{name}
%{python_sitelib}/*
%dir %{_sysconfdir}/%{name}
%config %{_sysconfdir}/%{name}/config.cfg
%{_unitdir}/*

%changelog
* Fri Jun 13 2014 Michael Simacek <msimacek@redhat.com> - 0.0.1-1
- Initial version
