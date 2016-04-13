import re
from setuptools import setup, find_packages

with open('koschei.spec') as specfile:
    for line in specfile:
        match = re.match(r'^Version:\s*([0-9.]+)', line)
        if match:
            version = match.group(1)

setup(
    name='koschei',
    version=version,
    description='',
    author='',
    author_email='',
    url='',
    packages=find_packages(exclude=["test"]),
    include_package_data=True,
    test_suite='nose.collector',
)
