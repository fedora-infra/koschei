from datetime import datetime
from flask import Flask
from sqlalchemy.orm import joinedload

from .models import Session, Package
from .reporter import jinja_env
from . import util

app = Flask('Koschei')
app.config.from_object(util.config['flask'])

@app.route('/')
def frontpage():
    since = datetime.min
    until = datetime.now()
    session = Session()
    packages = session.query(Package)\
                      .options(joinedload(Package.last_build))\
                      .order_by(Package.id).all()
    return jinja_env.get_template("frontpage.html")\
                    .render(packages=packages, since=since, until=until)

if __name__ == '__main__':
    app.run()
