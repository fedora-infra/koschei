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
    log.info('Intiating koji build for {name}:\n\tsource={source}\
              \n\ttarget={target}\n\tbuild_opts={build_opts}'.format(name=name,
                  target=target, source=source, build_opts=build_opts))
    if not dry_run:
        task_id = session.build(source, target, build_opts)
        log.info('Submitted koji scratch build for {name}, task_id={task_id}'\
                  .format(name=name, task_id=task_id))
        return task_id

def download_task_output(session, task_id, output_dir, filename_predicate=None):
    subtasks = session.getTaskChildren(task_id)
    build_tasks = [task for task in subtasks if task['method'] == 'buildArch']
    for task in build_tasks:
        files = session.listTaskOutput(task['id'])
        downloads = filter(filename_predicate, files)
        for download in downloads:
            log.debug('Downloading {} (task_id={}) to {}'\
                      .format(download, task['id'], output_dir))
            with open(os.path.join(output_dir, download), 'w') as new_file:
                new_file.write(session.downloadTaskOutput(task['id'], download))
