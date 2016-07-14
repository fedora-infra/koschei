#!/usr/bin/python
import logging

from koschei.backend.main import main

logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

if __name__ == '__main__':
    main()
