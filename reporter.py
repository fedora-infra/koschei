#!/usr/bin/python
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from models import Session, Package

jinja_env = Environment(loader=FileSystemLoader('./report-templates'))

def date_filter(date):
    return date.strftime("%x %X")

jinja_env.filters['date'] = date_filter

def generate_report(template, since, until):
    session = Session()
    template = jinja_env.get_template(template)
    packages = session.query(Package).filter_by(watched=True)
    return template.render(packages=packages, since=since, until=until)

if __name__ == '__main__':
    since = datetime.min
    until = datetime.now()
    print generate_report('base-report.html', since, until)
