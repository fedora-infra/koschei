from datetime import datetime
from flask import Flask, abort, render_template
from sqlalchemy.orm import scoped_session, sessionmaker, joinedload

from .models import engine, Package
from . import util

dirs = util.config['directories']
app = Flask('koschei', template_folder=dirs['templates'],
            static_folder=dirs['static'])
app.config.from_object(util.config['flask'])

db_session = scoped_session(sessionmaker(autocommit=False, bind=engine))

# Following will make pylint shut up about missing query method
if False:
    db_session.query = lambda *args: None

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

@app.template_filter('date')
def date_filter(date):
    return date.strftime("%F %T") if date else ''

@app.context_processor
def inject_dates():
    return {'since': datetime.min, 'until': datetime.now()}

@app.route('/')
def frontpage():
    packages = db_session.query(Package)\
                         .options(joinedload(Package.last_build))\
                         .order_by(Package.id).all()
    return render_template("frontpage.html", packages=packages)

@app.route('/package/<name>.html')
def package_detail(name):
    package = db_session.query(Package).filter_by(name=name).first()
    if not package:
        abort(404)
    return render_template("package-detail.html", package=package)


if __name__ == '__main__':
    app.run()
