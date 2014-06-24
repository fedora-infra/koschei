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
Requires:       python-jinja2
Requires:       python-hawkey

%description
TBD.

%prep
%setup -q -c -n %{name}

%build
%{__python} setup.py build

%install
%{__python} setup.py install --skip-build --root %{buildroot}

mkdir -p %{buildroot}%{_sysconfdir}/%{name}
cp -p config.json %{buildroot}%{_sysconfdir}/%{name}/

install -dm 755 %{buildroot}%{_unitdir}
for unit in systemd/*; do
    install -pm 644 $unit %{buildroot}%{_unitdir}/
done

mkdir -p %{buildroot}%{_bindir}
install -pm 755 admin.py %{buildroot}%{_bindir}/koschei-admin

mkdir -p %{buildroot}%{_localstatedir}/cache/%{name}

mkdir -p %{buildroot}%{_datadir}/%{name}
cp -pr report-templates %{buildroot}%{_datadir}/%{name}/

%post
%systemd_post koschei-scheduler.service
%systemd_post koschei-submitter.service
%systemd_post koschei-watcher.service
%systemd_post koschei-reporter.service
%systemd_post koschei-log-dowloader.service

%preun
%systemd_preun koschei-scheduler.service
%systemd_preun koschei-submitter.service
%systemd_preun koschei-watcher.service
%systemd_preun koschei-reporter.service
%systemd_preun koschei-log-dowloader.service

%postun
%systemd_postun_with_restart koschei-scheduler.service
%systemd_postun_with_restart koschei-submitter.service
%systemd_postun_with_restart koschei-watcher.service
%systemd_postun_with_restart koschei-reporter.service
%systemd_postun_with_restart koschei-log-dowloader.service

%files
%doc LICENSE.txt
%{_bindir}/koschei-admin
%{_datadir}/%{name}
%{python_sitelib}/*
%dir %{_sysconfdir}/%{name}
%config(noreplace) %{_sysconfdir}/%{name}/config.json
%{_unitdir}/*

%changelog
* Fri Jun 13 2014 Michael Simacek <msimacek@redhat.com> - 0.0.1-1
- Initial version
