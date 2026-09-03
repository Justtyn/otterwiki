# vim: set et ts=8 sts=4 sw=4 ai:

"""验证离线镜像的发布顺序和失败处理，确保不调用宿主机 Python。"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def ci_build(tmp_path):
    if not shutil.which('bash'):
        pytest.skip('构建脚本测试需要 Bash')
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / 'project with spaces'
    project.mkdir()
    shutil.copyfile(root / 'build.sh', project / 'build.sh')
    tools = tmp_path / 'bin'
    tools.mkdir()
    log = tmp_path / 'calls.log'
    wheels = tmp_path / 'offline wheels'
    wheels.mkdir()
    (wheels / 'Flask-3.1.3-py3-none-any.whl').write_text('offline dependency')
    (wheels / 'otterwiki-2.23.0-py3-none-any.whl').write_text(
        'old application'
    )
    for name in ('python', 'python3', 'python3.11', 'pip', 'pip3'):
        forbidden = tools / name
        forbidden.write_text('#!/usr/bin/env bash\nexit 80\n')
        forbidden.chmod(0o755)
    docker = tools / 'docker'
    docker.write_text(
        '#!/usr/bin/env bash\n'
        'printf "docker %s\\n" "$*" >> "$MOCK_LOG"\n'
        '[[ "$1" != buildx ]] || exit 82\n'
        'case "${MOCK_FAIL:-}:$1" in\n'
        '  info:info) exit 11 ;;\n'
        '  image:image) exit 12 ;;\n'
        '  build:build) exit 13 ;;\n'
        '  test:run) exit 14 ;;\n'
        '  push:push) exit 15 ;;\n'
        'esac\n'
        'if [[ "$1" == build ]]; then\n'
        '  [[ "$DOCKER_BUILDKIT" == 0 ]] || exit 83\n'
        '  [[ -s target/wheels/Flask-3.1.3-py3-none-any.whl ]] || exit 84\n'
        'fi\n'
    )
    docker.chmod(0o755)
    sudo = tools / 'sudo'
    sudo.write_text(
        '#!/usr/bin/env bash\n'
        'printf "sudo %s\\n" "$*" >> "$MOCK_LOG"\n'
        '[[ "$1" == -n ]] || exit 85\n'
        'shift\n'
        'exec "$@"\n'
    )
    sudo.chmod(0o755)

    def run(*args, **overrides):
        log.write_text('')
        env = {
            'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}',
            'WHEELHOUSE': str(wheels),
            'BASE_IMAGE': 'registry.example.com/otterwiki-runtime:2.23.0',
            'MOCK_LOG': str(log),
            **overrides,
        }
        result = subprocess.run(
            ['bash', str(project / 'build.sh'), *args],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
        )
        return result, log.read_text(), project / 'target'

    return run


def test_build_offline_then_push(ci_build):
    result, calls, target = ci_build('dev', '1.0.0')
    assert result.returncode == 0, result.stderr
    assert calls.index('docker build') < calls.index('docker push')
    assert '--network none --pull=false' in calls
    assert 'docker run' not in calls
    assert (target / 'wheels/Flask-3.1.3-py3-none-any.whl').exists()
    assert not (target / 'wheels/otterwiki-2.23.0-py3-none-any.whl').exists()
    assert 'PUSHED=true' in (target / 'image.env').read_text()


@pytest.mark.parametrize('failure', ['info', 'image', 'build', 'test', 'push'])
def test_failure_removes_previous_success_and_stops_publication(
    ci_build, failure
):
    result, _, target = ci_build('dev', '1.0', PUSH_IMAGE='false')
    assert result.returncode == 0
    assert (target / 'image-name.txt').exists()
    result, calls, target = ci_build(
        'dev', '1.0', MOCK_FAIL=failure, SKIP_TESTS='false'
    )
    assert result.returncode != 0
    assert not (target / 'image-name.txt').exists()
    assert not (target / 'image.env').exists()
    if failure != 'push':
        assert 'docker push' not in calls


def test_optional_tests_use_runtime_image_and_gate_push(ci_build):
    result, calls, _ = ci_build('dev', '1.0', SKIP_TESTS='false')
    assert result.returncode == 0, result.stderr
    assert (
        calls.index('docker build')
        < calls.index('docker run')
        < calls.index('docker push')
    )
    assert 'run --rm --network none --entrypoint /bin/sh' in calls
    assert '--no-index --find-links=/src/target/wheels' in calls
    assert 'OTTERWIKI_SETTINGS=""' in calls


def test_sudo_and_build_only_with_custom_image(ci_build):
    result, calls, target = ci_build(
        'test',
        '2.0',
        DOCKER_USE_SUDO='true',
        PUSH_IMAGE='false',
        IMAGE_TAG='manual-tag',
    )
    assert result.returncode == 0, result.stderr
    assert 'sudo -n docker build' in calls
    assert 'docker push' not in calls
    assert (
        (target / 'image-name.txt').read_text().strip().endswith(':manual-tag')
    )
    assert 'PUSHED=false' in (target / 'image.env').read_text()


@pytest.mark.parametrize(
    'args, overrides',
    [
        ([], {}),
        (['', '1.0'], {}),
        (['dev', 'bad/tag'], {}),
        (['dev', '1.0'], {'BASE_IMAGE': ''}),
        (['dev', '1.0'], {'WHEELHOUSE': '/missing/wheels'}),
        (['dev', '1.0'], {'PUSH_IMAGE': '1'}),
    ],
)
def test_invalid_configuration_fails_before_external_commands(
    ci_build, args, overrides
):
    result, calls, _ = ci_build(*args, **overrides)
    assert result.returncode != 0
    assert calls == ''
