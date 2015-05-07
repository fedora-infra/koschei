%bcond_without tests

Name:           koschei
Version:        0.2
Release:        1%{?dist}
Summary:        Continuous integration for Fedora packages
License:        GPLv2+
URL:            https://github.com/msimacek/%{name}
Source0:        https://github.com/msimacek/%{name}/archive/%{version}.tar.gz#/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python2-devel
BuildRequires:  python-setuptools
BuildRequires:  systemd

%if %{with tests}
BuildRequires:       python-nose
BuildRequires:       python-mock
BuildRequires:       python-sqlalchemy
BuildRequires:       koji
BuildRequires:       python-hawkey
BuildRequires:       python-librepo
BuildRequires:       rpm-python
%if 0%{?fedora} < 22 && !0%{?rhel}
BuildRequires:       dnf
%else
BuildRequires:       python-dnf
%endif
BuildRequires:       fedmsg
%endif

%if 0%{?fedora} < 22 && !0%{?rhel}
Requires:       dnf
%else
Requires:       python-dnf
%endif
Requires:       python-sqlalchemy
Requires:       koji
Requires:       fedmsg
Requires:       python-fedmsg-meta-fedora-infrastructure
Requires:       python-psycopg2
Requires:       createrepo_c
Requires:       curl
Requires:       python-jinja2
Requires:       python-hawkey
Requires:       python-alembic
Requires:       python-flask
Requires:       python-flask-sqlalchemy
Requires:       python-flask-openid
Requires:       mod_wsgi
Requires:       httpd
Requires:       python-librepo
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
%setup -q

sed 's|@CACHEDIR@|%{_localstatedir}/cache/%{name}|g
     s|@DATADIR@|%{_datadir}/%{name}|g
     s|@STATEDIR@|%{_sharedstatedir}/%{name}|g' config.cfg.template > config.cfg

%build
%{__python2} setup.py build

%install
%{__python2} setup.py install --skip-build --root %{buildroot}

mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_datadir}/%{name}
mkdir -p %{buildroot}%{_sysconfdir}/%{name}
mkdir -p %{buildroot}%{_sysconfdir}/httpd/conf.d

cp -p empty_config.cfg %{buildroot}%{_sysconfdir}/%{name}/config.cfg
cp -p empty_admin_config.cfg %{buildroot}%{_sysconfdir}/%{name}/config-admin.cfg
cp -p config.cfg %{buildroot}%{_datadir}/koschei/

install -dm 755 %{buildroot}%{_unitdir}
for unit in systemd/*; do
    install -pm 644 $unit %{buildroot}%{_unitdir}/
done

install -pm 755 admin.py %{buildroot}%{_bindir}/%{name}-admin

install -dm 755 %{buildroot}%{_localstatedir}/cache/%{name}/repodata
install -dm 755 %{buildroot}%{_localstatedir}/cache/%{name}/srpms
install -dm 755 %{buildroot}%{_sharedstatedir}/%{name}

cp -pr templates %{buildroot}%{_datadir}/%{name}/

cp -pr alembic/ alembic.ini %{buildroot}%{_datadir}/%{name}/
cp -pr static %{buildroot}%{_datadir}/%{name}/
cp -p %{name}.wsgi %{buildroot}%{_datadir}/%{name}/
cp -p httpd.conf %{buildroot}%{_sysconfdir}/httpd/conf.d/%{name}.conf

%if %{with tests}
%check
%{__python2} setup.py test
%endif

%pre
getent group %{name} >/dev/null || groupadd -r %{name}
# services and koschei-admin script is supposed to be run as this user
getent passwd %{name} >/dev/null || \
    useradd -r -g %{name} -d %{_datadir}/%{name} -s /bin/sh \
    -c "Runs %{name} services" %{name}
exit 0

# Workaround for RPM bug #646523 - can't change symlink to directory
%pretrans -p <lua>
dir = "%{_datadir}/%{name}/static"
dummy = posix.readlink(dir) and os.remove(dir)

%post
%systemd_post %{name}-scheduler.service
%systemd_post %{name}-watcher.service
%systemd_post %{name}-polling.service
%systemd_post %{name}-resolver.service

%preun
%systemd_preun %{name}-scheduler.service
%systemd_preun %{name}-watcher.service
%systemd_preun %{name}-polling.service
%systemd_preun %{name}-resolver.service

%postun
%systemd_postun %{name}-scheduler.service
%systemd_postun %{name}-watcher.service
%systemd_postun %{name}-polling.service
%systemd_postun %{name}-resolver.service

%files
%doc LICENSE.txt
%{_bindir}/%{name}-admin
%{_datadir}/%{name}
%attr(755, %{name}, %{name}) %{_localstatedir}/cache/%{name}
%attr(755, %{name}, %{name}) %{_sharedstatedir}/%{name}
%{python2_sitelib}/*
%dir %{_sysconfdir}/%{name}
%config(noreplace) %{_sysconfdir}/%{name}/config.cfg
%config(noreplace) %{_sysconfdir}/%{name}/config-admin.cfg
%config(noreplace) %{_sysconfdir}/httpd/conf.d/%{name}.conf
%{_unitdir}/*

%changelog
* Thu Mar 12 2015 Michael Simacek <msimacek@redhat.com> - 0.2-1
- Update to version 0.2

* Mon Sep 01 2014 Michael Simacek <msimacek@redhat.com> - 0.1-2
- Fixed BR python-devel -> python2-devel
- Fixed changelog format
- Added noreplace to httpd config
- Replaced name occurences with macro

* Fri Jun 13 2014 Michael Simacek <msimacek@redhat.com> - 0.1-1
- Initial version
