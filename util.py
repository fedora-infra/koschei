from __future__ import print_function

import os
import koji
import logging

log = logging.getLogger('util')

server = 'http://koji.fedoraproject.org/kojihub'
cert = os.path.expanduser('~/.fedora.cert')
ca_cert = os.path.expanduser('~/.fedora-server-ca.cert')

dry_run = True

server_opts = {
}

def create_koji_session(anonymous=False):
    koji_session = koji.ClientSession(server, server_opts)
    if not anonymous:
        koji_session.ssl_login(cert, ca_cert, ca_cert)
    return koji_session

def koji_scratch_build(session, name):
    build_opts = {
            'scratch': True,
        }
    git_url = 'git://pkgs.fedoraproject.org/{}'.format(name)
    #TODO use pygit
    import subprocess
    commits = subprocess.check_output(['git', 'ls-remote', git_url])
    commit_id = [ci.split('\t')[0] for ci in commits.split('\n')
                 if ci.endswith('refs/heads/master')][0]
    source = '{}?#{}'.format(git_url, commit_id)
    target = 'f21-candidate'
    log.debug('Intiating koji build for {name}:\n\tsource={source}\
              \n\ttarget={target}\n\tbuild_opts={build_opts}'.format(**locals()))
    if not dry_run:
        session.build(source, target, build_opts)
