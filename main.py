#!/usr/bin/python
# Copyright (C) 2014  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>

import inspect
import logging
import sys
import threading
import time
import argparse

import models
import scheduler
import submitter
import util
import plugins

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(logging.StreamHandler(sys.stderr))

stop_event = threading.Event()

def launch_task(fn, sleep_interval=3, thread=True):
    def inner():
        argspec = inspect.getargspec(fn)
        arg_names = argspec.args
        kwargs = {}
        db_session = None
        koji_session = None
        if 'db_session' in arg_names:
            db_session = models.Session()
            kwargs['db_session'] = db_session
        if 'koji_session' in arg_names:
            koji_session = util.create_koji_session()
            kwargs['koji_session'] = koji_session
        try:
            while True:
                fn(**kwargs)
                stop_event.wait(timeout=sleep_interval)
                if stop_event.is_set():
                    return
        finally:
            if db_session:
                db_session.close_all()
            if koji_session:
                koji_session.logout()
    thread = threading.Thread(target=inner)
    thread.start()
    return thread

def main_process():
    db_session = models.Session()
    while True:
        time.sleep(2)
        plugins.call_hooks('timer_tick', db_session)
        db_session.commit()

def main():
    plugins.load_plugins()
    try:
        launch_task(submitter.submit_builds)
        launch_task(submitter.poll_tasks, sleep_interval=120)
        launch_task(submitter.download_logs)
        launch_task(scheduler.schedule_builds)
        main_process()
    except (KeyboardInterrupt, Exception):
        stop_event.set()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    options = parser.parse_args()
    util.dry_run = options.dry_run
    main()

