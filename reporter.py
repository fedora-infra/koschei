#!/usr/bin/python
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from models import Session, Package

jinja_env = Environment(loader=FileSystemLoader('./report-templates'))

def generate_report(template, since):
    session = Session()
    template = jinja_env.get_template(template)
    packages = session.query(Package).filter_by(watched=True)
    return template.render(packages=packages, since=since)

if __name__ == '__main__':
    since = datetime.min
    print generate_report('base-report.html', since)
