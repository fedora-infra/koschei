from setuptools import setup, find_packages

setup(
    name='koschei',
    version='0.0.1',
    description='',
    author='',
    author_email='',
    url='',
    install_requires=["fedmsg"],
    entry_points={
        'moksha.consumer': (
            'koschei = watcher:KojiWatcher',
        ),
    },
    packages=find_packages(),
    include_package_data=True,
)
