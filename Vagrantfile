# -*- mode: ruby -*-
# vi: set ft=ruby :
#
$SCRIPT = <<-SHELL
# install deps
yum -y install epel-release yum-utils
sed -n '/%{name}/d;/^Requires:\\s*/s///p' /vagrant/koschei.spec | sort -u | xargs yum -y install
# setup config, dirs and symlinks
mkdir -p /home/vagrant/cache /usr/share/koschei /etc/koschei
ln -s /vagrant/config.cfg.template /usr/share/koschei/config.cfg
ln -s /vagrant/aux/vagrant-config.cfg /etc/koschei/config-backend.cfg
ln -s /vagrant/aux/vagrant-config.cfg /etc/koschei/config-admin.cfg
ln -s /vagrant/aux/vagrant-config.cfg /etc/koschei/config-frontend.cfg
ln -s /vagrant/koschei /usr/lib/python2.7/site-packages/koschei
ln -s /vagrant/static /usr/share/koschei/static
ln -s /vagrant/admin.py /usr/bin/koschei-admin
ln -s /vagrant/httpd.conf /etc/httpd/conf.d/koschei.conf
ln -s /vagrant/koschei.wsgi /usr/share/koschei/koschei.wsgi
adduser koschei
# setup DB
yum -y install postgresql-server
postgresql-setup initdb
cat > /var/lib/pgsql/data/pg_hba.conf << EOF
local all all              trust
host  all all 127.0.0.1/32 trust
host  all all ::1/128      trust
EOF
systemctl start postgresql
sudo -u postgres createuser -ls root
createuser -ls vagrant
createuser -ls koschei
createdb koschei
koschei-admin create-db
koschei-admin create-collection f25 -d 'Fedora Rawhide' --branch master -b f25-build -t f25
# make dwalsh cry
setenforce 0
# start httpd
systemctl start httpd
SHELL

Vagrant.configure(2) do |config|
  config.vm.box = "centos/7"
  config.vm.network "forwarded_port", guest: 80, host: 5000
  config.vm.network "private_network", ip: "192.168.33.10"
  # disable centos default sync point
  config.vm.synced_folder ".", "/home/vagrant/sync", disabled: true
  config.vm.synced_folder ".", "/vagrant"
  config.vm.provision "shell", inline: $SCRIPT
end
