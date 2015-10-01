Developer instance in a VM
==========================

This HOWTO describes how to easily install Koschei development instance in a virtual machine.  It focuses on easy setup for Koschei development, not for production.


Creating virtual machine
------------------------

Obtain latest Fedora cloud image:

    $ wget https://download.fedoraproject.org/pub/fedora/linux/releases/22/Cloud/x86_64/Images/Fedora-Cloud-Base-22-20150521.x86_64.qcow2

Generate CDROM ISO image for use by cloud-init (replace "ssh-rsa ..." with your SSH public key):

    $ cat >meta-data <<EOF
    instance-id: koschei01
    local-hostname: koschei01
    EOF

    $ cat >user-data <<EOF
    #cloud-config
    password: koschei
    chpasswd: { expire: False }
    ssh_pwauth: True
    ssh_authorized_keys:
       - ssh-rsa ...
    EOF

    # genisoimage -output /var/lib/libvirt/images/koschei01-cidata.iso -volid cidata -joliet -rock user-data meta-data

Prepare 20 GB disk image:

    # qemu-img create -f qcow2 /var/lib/libvirt/images/koschei01.qcow2 20G
    # virt-resize --expand /dev/sda1 Fedora-Cloud-Base-22-20150521.x86_64.qcow2 /var/lib/libvirt/images/koschei01.qcow2

Launch virtual machine:

    # virt-install --import --noautoconsole --name koschei01 --ram 2048 --disk /var/lib/libvirt/images/koschei01.qcow2 --disk /var/lib/libvirt/images/koschei01-cidata.iso,device=cdrom

Determine virtual machine MAC and IP addresses:

    # virsh dumpxml koschei01 | grep \<mac
    $ arp -an | grep 52:54:00:c7:7b:8c

Log into VM as root:

    # ssh fedora@192.168.122.81
    (fedora)$ sudo su -

Turn off SELinux (you can leave it enabled, but then you can expect additional steps, which are not documented here):

    # setenforce 0

Install latest updates:

    # dnf -y update


Installing PostgreSQL
---------------------

Install DB server and initialize database:

    # dnf -y install postgresql-server
    # postgresql-setup --initdb

Allow `koscheiuser` and `koscheiadmin` users (which will be created later) to use MD5-based password authentication:

    # sed -i '1ilocal koschei koscheiuser md5' /var/lib/pgsql/data/pg_hba.conf
    # sed -i '1ilocal koschei koscheiadmin md5' /var/lib/pgsql/data/pg_hba.conf

Start DB server and enable it to automatically start on boot:

    # systemctl enable postgresql
    # systemctl start postgresql

From now on all DB configuration will be done as user postgres:

    # su - postgres

Create users `koscheiuser` and `koscheiadmin`.  When asked for passwords, type "userpassword" and "adminpassword" (respectively).

    (postgres)$ createuser -P koscheiuser
    (postgres)$ createuser -P koscheiadmin

Create Koschei database:

    (postgres)$ createdb -O koscheiadmin koschei

Optionally you can import database dump from production instance (otherwise you will need to import DB schema, which will be described later):

    (postgres)$ curl -s https://infrastructure.fedoraproject.org/infra/db-dumps/koschei.dump.xz | xzcat | psql


Installing Koschei
------------------

Install main Koschei package:

    # dnf -y install koschei

Install [config.cfg](https://github.com/msimacek/koschei/blob/master/doc/howto/devel-instance-in-vm/config.cfg) (main config file) into `/etc/koschei/` (you can adjust the config as needed):

    # curl -s https://raw.githubusercontent.com/msimacek/koschei/master/doc/howto/devel-instance-in-vm/config.cfg >/etc/koschei/config.cfg

Likewise, install [config-admin.cfg](https://github.com/msimacek/koschei/blob/master/doc/howto/devel-instance-in-vm/config-admin.cfg) (credentials for admin user):

    # curl -s https://raw.githubusercontent.com/msimacek/koschei/master/doc/howto/devel-instance-in-vm/config-admin.cfg >/etc/koschei/config-admin.cfg

Install Fedora server CA certificate:

    # curl -s https://admin.fedoraproject.org/accounts/fedora-server-ca.cert >/etc/koschei/fedora-ca.cert

Optionally install Koji user certificate as /etc/koschei/koschei.pem (only if you want your Koschei to schedule builds on Koji, it's not needed or recommended for running passive Koschei instance).

If you skipped importing DB dump when creating database, you will now need to import DB schema to have empty DB with proper structure:

    # su - koschei
    (koschei)$ koschei-admin createdb


Running frontend
----------------

Once Koschei is configured, frontend can be started by running:

    # systemctl start httpd

After that Koschei Web frontend should be running at http://192.168.122.81/ (where 192.168.122.81 is IP address of your virtual machine).
