Koschei Koji plugin
===================

Installation
------------

First install plugin file in Koji hub file system:

    mkdir -p /usr/lib/koji-hub-plugins/
    cp koji/plugin/koschei.py /usr/lib/koji-hub-plugins/

And then enable it in hub config.  `/etc/koji-hub/hub.conf` should
contain:

    PluginPath = /usr/lib/koji-hub-plugins
    Plugins = koschei

Since repos created by Koschei plugin don't physically exist on hub,
appropriate redirect must be added.  Create
`/etc/httpd/conf.d/koschei-repos.conf` with the following content:

    RewriteEngine on
    RewriteRule "^/kojifiles/repos/(.*)" "http://master-koji.example.com/kojifiles/repos/$1" [R,L]

Koji hub needs to be restarted for the changes to take effect:

    systemctl restart httpd

Now Koschei Koji plugin should be installed.  To verify that, run
`koji list-api` command -- it should now display `koscheiCreateRepo`
as one of available API calls.

Usage
-----

Example plugin usage from Python:

    import koji
    ks = koji.ClientSession('http://koji.example.com/kojihub')
    ks.login()
    ks.koscheiCreateRepo(582920, 'f24-build')
