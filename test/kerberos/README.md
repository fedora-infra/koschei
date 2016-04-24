Testing Kerberos authentication
-------------------------------

This document describes how to setup a Kerberos KDC for testing
Koschei authentication.  The following way does not require changing
your system-wide Kerberos settings.

First, disable SELinux. This step can be skipped, but you'll need to
somehow allow httpd to read keytab and krb5.conf.

    setenforce 0

Set paths to Kerberos config files.  All Kerberos commands and Firefox
need them set.

    export KRB5_CONFIG=/home/kojan/git/koschei/test/kerberos/krb5.conf
    export KRB5_KDC_PROFILE=/home/kojan/git/koschei/test/kerberos/kdc.conf

Initialize Kerberos database.

    kdb5_util create -s

Add "jsmith" principal.  Add server principals for localhost and
127.0.0.1.  Export keytab.

    kadmin.local
    addprinc jsmith
    addprinc -randkey HTTP/127.0.0.1
    addprinc -randkey HTTP/localhost
    ktadd -k /etc/krb5.keytab HTTP/127.0.0.1
    ktadd -k /etc/krb5.keytab HTTP/localhost
    quit

Ensure keytab is secure, but readable by httpd.

    chmod 400 /etc/krb5.keytab
    chown apache:apache /etc/krb5.keytab
    systemctl reload httpd

Start KDC daemon (it will run in background, unless -n is given).

    krb5kdc

Obtain ticket for jsmith.

    kinit jsmith
    klist

Configure Firefox - browse to `about:config` and add hostname and/or
IP address of Koschei frontend server to both
`network.negotiate-auth.delegation-uris` and
`network.negotiate-auth.trusted-uris`. For example you can set them
both to `127.0.0.1,.redhat.com`.

In case of problems, you can enable Kerberos debugging in Firefox.

    export NSPR_LOG_MODULES=negotiateauth:5
    export NSPR_LOG_FILE=/tmp/moz.log
