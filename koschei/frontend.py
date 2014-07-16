from datetime import datetime
from flask import Flask, abort
from sqlalchemy.orm import scoped_session, sessionmaker, joinedload

from .models import engine, Package
from .reporter import jinja_env
from . import util

app = Flask('Koschei')
app.config.from_object(util.config['flask'])

db_session = scoped_session(sessionmaker(autocommit=False, bind=engine))

# Following will make pylint shut up about missing query method
if False:
    db_session.query = lambda *args: None

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

@app.route('/')
def frontpage():
    since = datetime.min
    until = datetime.now()
    packages = db_session.query(Package)\
                         .options(joinedload(Package.last_build))\
                         .order_by(Package.id).all()
    return jinja_env.get_template("frontpage.html")\
                    .render(packages=packages, since=since, until=until)

@app.route('/package/<name>.html')
def package_detail(name):
    package = db_session.query(Package).filter_by(name=name).first()
    if not package:
        abort(404)
    return jinja_env.get_template("package-detail.html").render(package=package)


if __name__ == '__main__':
    app.run()
