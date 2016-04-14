from __future__ import print_function

import os
import platform

from koschei.config import load_config, get_config

testdir = os.path.dirname(os.path.realpath(__file__))

is_x86_64 = platform.machine() == 'x86_64'

load_config(['{0}/../config.cfg.template'.format(testdir),
             '{0}/test_config.cfg'.format(testdir)])

config = get_config(None)
