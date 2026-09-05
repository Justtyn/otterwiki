"""仅供隔离网关验收：将真实迁移延迟 65 秒，不用于部署。"""

import time
from otterwiki.server import app
from otterwiki import document_import

_original = document_import._run_migration


def slow_migration(*args, **kwargs):
    time.sleep(65)
    return _original(*args, **kwargs)


document_import._run_migration = slow_migration
