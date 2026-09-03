# vim: set et ts=8 sts=4 sw=4 ai:
"""验证离线材料准备失败可重试，且不会覆盖已完成的材料。"""

import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


@pytest.fixture
def offline_prepare(tmp_path):
    if not shutil.which('bash'):
        pytest.skip('离线准备脚本测试需要 Bash')
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / 'project with spaces'
    project.mkdir()
    for name in ('prepare-offline.sh', 'pyproject.toml'):
        shutil.copyfile(root / name, project / name)
    tools = tmp_path / 'bin'
    tools.mkdir()
    counter = tmp_path / 'pull-count'
    docker = tools / 'docker'
    docker.write_text(
        '#!/usr/bin/env bash\n'
        'set -eu\n'
        'case "$1" in\n'
        'pull)\n'
        '  count=0; if [[ -f "$MOCK_COUNTER" ]]; then read -r count < "$MOCK_COUNTER"; fi\n'
        '  count=$((count + 1)); echo "$count" > "$MOCK_COUNTER"\n'
        '  [[ "${MOCK_FAIL:-}" != pull ]] || exit 11\n'
        '  [[ "${MOCK_FAIL:-}" != once || "$count" -gt 1 ]] || exit 12 ;;\n'
        'build) [[ "${MOCK_FAIL:-}" != runtime ]] || exit 15 ;;\n'
        'run)\n'
        '  if [[ "$*" == */src/prepare-wheels.py* ]]; then\n'
        '    [[ "${MOCK_FAIL:-}" != packages ]] || exit 13\n'
        '    for arg in "$@"; do\n'
        '      case "$arg" in *:/wheelhouse) echo wheel > "${arg%:/wheelhouse}/sample.whl" ;; esac\n'
        '    done\n'
        '  fi ;;\n'
        'save)\n'
        '  [[ "${MOCK_FAIL:-}" != save ]] || exit 14\n'
        '  echo image > "$3" ;;\n'
        'esac\n'
    )
    docker.chmod(0o755)
    sleep = tools / 'sleep'
    sleep.write_text('#!/usr/bin/env bash\nexit 0\n')
    sleep.chmod(0o755)

    def run(failure=''):
        result = subprocess.run(
            ['bash', str(project / 'prepare-offline.sh')],
            cwd=tmp_path,
            env={
                'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}',
                'MOCK_COUNTER': str(counter),
                'MOCK_FAIL': failure,
            },
            text=True,
            capture_output=True,
        )
        count = int(counter.read_text()) if counter.exists() else 0
        return result, count

    return project, run


def test_retry_and_recover_empty_directory(offline_prepare):
    project, run = offline_prepare
    (project / 'offline-bundle-bullseye/python-wheels').mkdir(parents=True)
    result, count = run('once')
    assert result.returncode == 0, result.stderr
    assert count == 2
    assert not list(project.glob('.offline-prep.*'))
    with tarfile.open(
        project / 'otterwiki-offline-bullseye.tar.gz'
    ) as archive:
        assert 'offline-bundle/python-wheels/sample.whl' in archive.getnames()
        assert 'offline-bundle/otterwiki-runtime.tar' in archive.getnames()


@pytest.mark.parametrize('failure', ['pull', 'runtime', 'packages', 'save'])
def test_failure_cleans_partial_outputs(offline_prepare, failure):
    project, run = offline_prepare
    result, count = run(failure)
    assert result.returncode != 0
    assert count == (3 if failure == 'pull' else 1)
    assert not (project / 'offline-bundle-bullseye').exists()
    assert not (project / 'otterwiki-offline-bullseye.tar.gz').exists()
    assert not list(project.glob('.offline-prep.*'))
    result, _ = run()
    assert result.returncode == 0, result.stderr


def test_existing_materials_are_preserved(offline_prepare):
    project, run = offline_prepare
    materials = project / 'offline-bundle-bullseye/python-wheels'
    materials.mkdir(parents=True)
    saved = materials / 'important.whl'
    saved.write_text('keep')
    result, count = run()
    assert result.returncode != 0
    assert count == 0
    assert saved.read_text() == 'keep'
