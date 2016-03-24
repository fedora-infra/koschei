import koji
import logging

from koschei import util
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
        remote_repo = util.get_latest_repo(secondary, collection.build_tag)
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
                for repo in primary.listExternalRepos(tag):
                    primary.removeExternalRepoFromTag(tag, repo['name'])
                repo = primary.createExternalRepo(
                    '{}-{}'.format(collection.name, remote_repo['id']),
                    repo_url)
                primary.addExternalRepoToTag(tag, repo['name'], 0)  # priority
                task_id = primary.newRepo(tag)
                db.add(RepoMapping(task_id=task_id,
                                   secondary_id=remote_repo['id']))
                db.commit()
            except koji.Fault:
                log.exception("Requesting new repo failed")
