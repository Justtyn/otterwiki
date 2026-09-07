#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""容器必须在 Web 进程启动前完成数据库迁移。"""

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    'path', ['docker/entrypoint.sh', 'docker/entrypoint-slim.sh']
)
def test_entrypoint_upgrades_database_before_starting_web(path):
    script = Path(path).read_text()
    migration = (
        '/opt/venv/bin/python -m flask --app otterwiki.server db upgrade'
    )

    assert script.index('for PLUGIN') < script.index(migration)
    assert script.index('cd /app', script.index('for PLUGIN')) < script.index(
        migration
    )
    assert script.index(migration) < script.index('exec "$@"')


def test_full_entrypoint_restores_app_data_ownership_after_migration():
    script = Path('docker/entrypoint.sh').read_text()
    migration = (
        '/opt/venv/bin/python -m flask --app otterwiki.server db upgrade'
    )

    assert script.index(migration) < script.rindex(
        'chown -R www-data:www-data /app-data'
    )
