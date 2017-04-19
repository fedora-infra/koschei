from koschei.config import load_config

load_config(['/usr/share/koschei/config.cfg', '/etc/koschei/config-frontend.cfg'])

from koschei.frontend import app as application
import koschei.frontend.api
import koschei.frontend.views
import koschei.frontend.auth
