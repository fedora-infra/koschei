from plugins import plugin
from models import Package, PriorityChange

@plugin('timer_tick')
def adjust_priorities(db_session):
    package_query = db_session.query(Package).filter_by(watched=True)
    for package in package_query:
        time_priority = package.priority_changes.filter_by(plugin_name='time').first()
        if not time_priority:
            time_priority = PriorityChange(plugin_name='time', value=1, effective=True,
                                           comment='Time since last rebuild',
                                           package_id=package.id)
            db_session.add(time_priority)
        else:
            time_priority.value += 1
        db_session.commit()
