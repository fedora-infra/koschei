from plugins import plugin
from models import Package

@plugin('timer_tick')
def adjust_priorities(db_session):
    package_query = db_session.query(Package)
    package_query.update({Package.priority: Package.priority + 1})
