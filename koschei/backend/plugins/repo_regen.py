import logging

import koji

from koschei.backend import koji_util
from koschei.models import Collection, RepoMapping
from koschei.plugin import listen_event

log = logging.getLogger('koschei.repo_regen_plugin')


@listen_event('polling_event')
def poll_secondary_repo(backend):
    log.debug("Polling new external repo")
    db = backend.db
    primary = backend.koji_sessions['primary']
    secondary = backend.koji_sessions['secondary']
    for collection in db.query(Collection).all():
        remote_repo = koji_util.get_latest_repo(secondary, collection.build_tag)
        mapping = db.query(RepoMapping)\
            .filter_by(secondary_id=remote_repo['id'])\
            .first()
        if not mapping:
            log.info("Requesting new repo for %s", collection)
            try:
                repo_url = secondary.config['repo_url']\
                    .format(build_tag=collection.build_tag,
                            repo_id=remote_repo['id'],
                            arch="$arch")
                tag = collection.build_tag
                for repo in primary.getTagExternalRepos(tag):
                    primary.removeExternalRepoFromTag(tag, repo['external_repo_id'])
                name = '{}-{}'.format(collection.name, remote_repo['id'])
                repo = (primary.getExternalRepo(name) or
                        primary.createExternalRepo(name, repo_url))
                primary.addExternalRepoToTag(tag, repo['name'], 0)  # priority
                task_id = primary.newRepo(tag)
                db.add(RepoMapping(task_id=task_id,
                                   secondary_id=remote_repo['id']))
                db.commit()
            except koji.GenericError:
                log.exception("Requesting new repo failed")
