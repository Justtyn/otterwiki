# vim: set et ts=8 sts=4 sw=4 ai:

"""整站往返、停站初始化、错误输入及切换恢复测试。"""

from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import zipfile

import git
import pytest

from otterwiki import site_backup as backup


@pytest.fixture
def site(test_client, create_app, tmp_path):
    from otterwiki.server import db
    from otterwiki.models import Drafts, Space, SpaceGitRepository
    from otterwiki.credentials import encrypt_secret
    from otterwiki.spaces import get_space_storage

    with create_app.app_context():
        extra = Space(slug='docs', name='产品文档', is_default=False)
        db.session.add(extra)
        db.session.commit()
        space_storage = get_space_storage(extra)
        space_storage.store(
            filename='guide.md',
            content='# 指南\n',
            author=('测试', 'test@example.org'),
            message='新增指南',
        )
        (Path(space_storage.path) / 'untracked.txt').write_text(
            '尚未提交', encoding='utf-8'
        )
        db.session.add(
            Drafts(pagepath='guide', space_id=extra.id, content='草稿')
        )
        db.session.add(
            SpaceGitRepository(
                space_id=extra.id,
                remote_url='https://example.org/wiki.git',
                auth_type='https',
                secret_ciphertext=encrypt_secret('仓库令牌'),
            )
        )
        db.session.commit()
        database = tmp_path / 'database.sqlite'
        connection = db.engine.raw_connection()
        with closing(sqlite3.connect(database)) as target:
            connection.driver_connection.backup(target)
        connection.close()
        config = dict(create_app.config)
        config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + str(database)
        config['SITE_NAME'] = '待备份网站'
        archive = tmp_path / 'site.zip'
        with closing(backup._connect(database)) as source:
            backup.create_archive(config, source, archive)
    return config, archive, test_client


def test_roundtrip_all_spaces_settings_history_and_credentials(site, tmp_path):
    config, archive, _ = site
    target_root = tmp_path / 'target'
    target_root.mkdir()
    target = dict(
        config,
        REPOSITORY=str(target_root / 'repository'),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + str(target_root / 'db.sqlite'),
        SPACES_ROOT=str(target_root / 'spaces'),
        SECRET_KEY='另一台服务器的不同密钥',
    )
    with (
        closing(
            backup._connect(backup.data_paths(config)['database'])
        ) as source,
        closing(sqlite3.connect(target_root / 'db.sqlite')) as output,
    ):
        source.backup(output)
    backup.restore_site(target, archive)
    with closing(backup._connect(target_root / 'db.sqlite')) as connection:
        preferences = dict(
            connection.execute('SELECT name,value FROM preferences')
        )
        assert preferences['SITE_NAME'] == '待备份网站'
        assert preferences[backup.SESSION_KEY]
        assert 'SECRET_KEY' not in preferences
        assert (
            connection.execute('SELECT content FROM drafts').fetchone()[0]
            == '草稿'
        )
        encrypted = connection.execute(
            'SELECT secret_ciphertext FROM space_git_repository'
        ).fetchone()[0]
        assert (
            backup._cipher(target['SECRET_KEY'])
            .decrypt(encrypted.encode())
            .decode()
            == '仓库令牌'
        )
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    with (
        git.Repo(target_root / 'repository') as restored,
        git.Repo(config['REPOSITORY']) as original,
    ):
        assert restored.head.commit.hexsha == original.head.commit.hexsha
    assert (
        target_root / 'spaces/2/repository/guide.md'
    ).read_text() == '# 指南\n'
    assert (
        target_root / 'spaces/2/repository/untracked.txt'
    ).read_text() == '尚未提交'
    assert not (backup.backup_root(target) / 'operation.json').exists()


def test_reset_clears_data_and_preserves_deployment_config(site):
    config, _, _ = site
    paths = backup.data_paths(config)
    paths['tasks'].mkdir(exist_ok=True)
    (paths['tasks'] / 'old-task.txt').write_text('旧任务')
    backup.restore_site(config)
    with closing(backup._connect(paths['database'])) as connection:
        for table in (
            'user',
            'space',
            'drafts',
            'group',
            'user_group',
            'space_git_repository',
            'git_sync_task',
        ):
            assert (
                connection.execute(
                    'SELECT count(*) FROM ' + backup._quote(table)
                ).fetchone()[0]
                == 0
            )
        preferences = dict(
            connection.execute('SELECT name,value FROM preferences')
        )
        assert preferences['SITE_NAME'] == backup.DEFAULT_CONFIG['SITE_NAME']
        assert preferences['DISABLE_REGISTRATION'] == 'False'
        assert preferences['ADMIN_USER_EMAIL'] == ''
        assert 'REPOSITORY' not in preferences
        assert preferences[backup.SESSION_KEY]
    assert not (paths['repository'] / 'home.md').exists()
    with git.Repo(paths['repository']) as repo:
        assert not repo.head.is_valid()
    assert list(paths['spaces'].iterdir()) == []
    assert list(paths['tasks'].iterdir()) == []


def test_pending_is_applied_before_startup_and_removed(site):
    config, archive, _ = site
    root = backup.backup_root(config)
    root.mkdir(exist_ok=True)
    (root / 'pending.zip').write_bytes(archive.read_bytes())
    backup.apply_pending(config)
    assert not (root / 'pending.zip').exists()
    backup.apply_pending(config)


def _rewrite(archive, target, name, data):
    with (
        zipfile.ZipFile(archive) as source,
        zipfile.ZipFile(target, 'w') as output,
    ):
        for info in source.infolist():
            if info.filename != name:
                output.writestr(info, source.read(info))
        output.writestr(name, data)


@pytest.mark.parametrize(
    'name',
    [
        '../escape',
        '/absolute',
        'repositories/1/../../escape',
        'repositories/1/.git/hooks/post-checkout',
        'repositories/1/.git/objects/info/alternates',
        'repositories/1/C:\\escape',
    ],
)
def test_unsafe_archive_does_not_change_site(site, tmp_path, name):
    config, archive, _ = site
    bad = tmp_path / 'bad.zip'
    _rewrite(archive, bad, name, b'bad')
    before = (Path(config['REPOSITORY']) / 'home.md').read_bytes()
    with pytest.raises(backup.BackupError):
        backup.restore_site(config, bad)
    assert (Path(config['REPOSITORY']) / 'home.md').read_bytes() == before
    with closing(
        backup._connect(backup.data_paths(config)['database'])
    ) as connection:
        assert (
            connection.execute('SELECT count(*) FROM user').fetchone()[0] > 0
        )


def test_zip_limits_symlink_and_duplicate_rejected(site, tmp_path):
    config, archive, _ = site
    config['SITE_BACKUP_MAX_FILES'] = 1
    with pytest.raises(backup.BackupError, match='数量'):
        backup.restore_site(config, archive)
    config['SITE_BACKUP_MAX_FILES'] = 200000
    bad = tmp_path / 'link.zip'
    with (
        zipfile.ZipFile(archive) as source,
        zipfile.ZipFile(bad, 'w') as output,
    ):
        for info in source.infolist():
            output.writestr(info, source.read(info))
        info = zipfile.ZipInfo('repositories/1/link')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, '/tmp')
    with pytest.raises(backup.BackupError, match='特殊文件'):
        backup.restore_site(config, bad)


def test_failure_during_switch_rolls_back_database_and_repositories(
    site, monkeypatch
):
    config, _, _ = site
    paths = backup.data_paths(config)
    original = paths['database'].read_bytes()
    home = (paths['repository'] / 'home.md').read_bytes()
    replace = backup.os.replace
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if not failed and Path(target) == paths['spaces']:
            failed = True
            raise OSError('模拟切换失败')
        return replace(source, target)

    monkeypatch.setattr(backup.os, 'replace', fail_once)
    with pytest.raises(OSError, match='模拟切换失败'):
        backup.restore_site(config)
    assert failed
    assert paths['database'].read_bytes() == original
    assert (paths['repository'] / 'home.md').read_bytes() == home
    assert (paths['spaces'] / '2/repository/guide.md').exists()
    assert not (paths['maintenance'] / 'operation.json').exists()


def test_web_export_and_csrf_permission_checks(test_client, create_app):
    response = test_client.get('/-/admin/site_backup')
    assert '网站备份与恢复' in response.get_data(as_text=True)
    response.close()
    response = test_client.post(
        '/-/admin/site_backup', data={'action': 'export'}
    )
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert 'manifest.json' in archive.namelist()
    response.close()
    client = create_app.test_client()
    response = client.post('/-/admin/site_backup', data={'action': 'export'})
    assert response.status_code == 302
    response.close()
    response = test_client.post(
        '/-/admin/site_backup',
        data={'action': 'export', 'csrf_token': 'invalid'},
    )
    assert response.status_code == 400
    response.close()


def test_web_stage_cancel_and_confirmation(site, create_app):
    config, archive, client = site
    # Web 测试使用内存数据库，校验目标指向同结构的文件快照。
    create_app.config['SQLALCHEMY_DATABASE_URI'] = config[
        'SQLALCHEMY_DATABASE_URI'
    ]
    response = client.post(
        '/-/admin/site_backup',
        data={'action': 'import', 'confirmation': 'wrong'},
    )
    assert response.status_code == 400
    assert '请输入' in response.get_data(as_text=True)
    response.close()
    response = client.post(
        '/-/admin/site_backup',
        data={
            'action': 'import',
            'confirmation': 'RESTORE OTTERWIKI',
            'backup': (io.BytesIO(archive.read_bytes()), 'site.zip'),
        },
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert '等待导入' in response.get_data(as_text=True)
    response.close()
    pending = backup.backup_root(config) / 'pending.zip'
    assert pending.exists()
    response = client.post('/-/admin/site_backup', data={'action': 'cancel'})
    assert response.status_code == 200
    response.close()
    assert not pending.exists()


def test_cli_reset_preview_does_not_modify_data(site, tmp_path):
    config, _, _ = site
    settings = tmp_path / 'cli.cfg'
    settings.write_text(
        '\n'.join(
            f'{key} = {value!r}'
            for key, value in config.items()
            if key in backup.DEFAULT_CONFIG
        )
    )
    before = backup.data_paths(config)['database'].read_bytes()
    result = subprocess.run(
        [
            'venv/bin/python',
            '-m',
            'otterwiki.site_backup',
            '--settings',
            str(settings),
            'reset',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '尚未执行' in result.stdout
    assert backup.data_paths(config)['database'].read_bytes() == before


@pytest.mark.parametrize("windows_gbk", [False, True])
def test_reset_bootstrap_first_admin_and_old_cookies(
    site, tmp_path, windows_gbk
):
    import os

    config, _, _ = site
    backup.restore_site(config)
    settings = tmp_path / 'bootstrap.cfg'
    config.update(HOME_PAGE='/-/old-external-home', DISABLE_REGISTRATION=True)
    settings.write_text(
        '\n'.join(
            f'{key} = {value!r}'
            for key, value in config.items()
            if key in backup.DEFAULT_CONFIG
        )
    )
    script = '''
from otterwiki.server import app, db
from otterwiki.auth import auth_manager
from otterwiki.models import Space
from pathlib import Path
assert app.config['SITE_NAME'] == 'An Otter Wiki'
assert app.config['DISABLE_REGISTRATION'] is False
assert app.config['HOME_PAGE'] == ''
assert app.config['SESSION_COOKIE_NAME'].startswith('session_')
assert app.config['REMEMBER_COOKIE_NAME'].startswith('remember_')
assert Path(app.config['REPOSITORY'], 'home.md').exists()
with app.test_request_context('/'):
    assert Space.query.filter_by(is_default=True).count() == 1
    auth_manager.create_user('new@example.org', '新管理员', 'password1234')
    user = auth_manager.User.query.filter_by(email='new@example.org').one()
    assert user.is_admin
'''
    if windows_gbk:
        # 在非 Windows 测试机复现中文 Windows 默认使用 GBK 读取文件。
        script = (
            r'''
import builtins
_original_open = builtins.open
def windows_open(file, mode="r", *args, **kwargs):
    if str(file).endswith("initial_home.md") and "b" not in mode:
        kwargs.setdefault("encoding", "gbk")
    return _original_open(file, mode, *args, **kwargs)
builtins.open = windows_open
'''
            + script
        )
    env = dict(os.environ, OTTERWIKI_SETTINGS=str(settings))
    result = subprocess.run(
        ['venv/bin/python', '-c', script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_generation_invalidates_password_reset_tokens(create_app):
    from otterwiki.helper import serialize, deserialize, SerializeError

    token = serialize('user@example.org', salt='recover-password')
    create_app.config['SITE_SESSION_GENERATION'] = 'new-generation'
    with pytest.raises(SerializeError):
        deserialize(token, salt='recover-password')
    new_token = serialize('user@example.org', salt='recover-password')
    assert (
        deserialize(new_token, salt='recover-password') == 'user@example.org'
    )


def test_export_preserves_unvisited_empty_space(site, create_app, tmp_path):
    from otterwiki.models import Space
    from otterwiki.server import db

    config, _, _ = site
    with create_app.app_context():
        db.session.add(Space(slug='empty', name='空空间', is_default=False))
        db.session.commit()
        archive = tmp_path / 'empty.zip'
        source = db.engine.raw_connection()
        try:
            backup.create_archive(config, source.driver_connection, archive)
        finally:
            source.close()
    extracted = tmp_path / 'empty-extracted'
    extracted.mkdir()
    manifest = backup.validate_archive(config, archive, extracted)
    assert len(manifest['spaces']) == 3


def test_schema_mismatch_and_foreign_key_corruption_rejected(site, tmp_path):
    config, archive, _ = site
    extracted = tmp_path / 'database-change'
    extracted.mkdir()
    with zipfile.ZipFile(archive) as source:
        database = extracted / 'database.sqlite'
        database.write_bytes(source.read('database.sqlite'))
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('UPDATE user_group SET user_id=99999')
        connection.commit()
    bad = tmp_path / 'orphan.zip'
    _rewrite(archive, bad, 'database.sqlite', database.read_bytes())
    with pytest.raises(backup.BackupError, match='关系不完整'):
        backup.restore_site(config, bad)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE unknown (id INTEGER)')
        connection.commit()
    _rewrite(archive, bad, 'database.sqlite', database.read_bytes())
    with pytest.raises(backup.BackupError, match='数据库结构不同'):
        backup.restore_site(config, bad)


def test_export_refuses_active_background_task(test_client, create_app):
    from otterwiki.import_runtime import ProcessLock, task_root

    lock = ProcessLock(task_root(create_app.config))
    assert lock.acquire()
    try:
        response = test_client.post(
            '/-/admin/site_backup', data={'action': 'export'}
        )
        assert response.status_code == 400
        assert '仍在执行' in response.get_data(as_text=True)
        response.close()
    finally:
        lock.release()


def test_non_admin_cannot_export_or_stage(test_client, create_app):
    from otterwiki.models import User
    from otterwiki.server import db

    with create_app.app_context():
        user = User.query.filter_by(email='mail@example.org').one()
        user.is_admin = False
        db.session.commit()
    for action in ('export', 'import', 'cancel'):
        response = test_client.post(
            '/-/admin/site_backup', data={'action': action}
        )
        assert response.status_code == 403
        response.close()


def test_site_gate_blocks_new_requests_until_stream_closed(create_app):
    import threading
    from otterwiki.site_backup_web import SiteGate

    gate = SiteGate(lambda env, start: [b'page'], create_app.config)
    # 两个尚未结束的请求，其中一个模拟导出请求自身。
    first = gate({}, lambda *args: None)
    second = gate({}, lambda *args: None)
    assert next(first) == b'page'
    entered = threading.Event()
    finish = threading.Event()

    def export():
        with gate.exclusive():
            entered.set()
            finish.wait(5)

    worker = threading.Thread(target=export)
    worker.start()
    first.close()
    assert entered.wait(2)
    statuses = []
    response = gate(
        {'REQUEST_METHOD': 'GET'},
        lambda status, headers: statuses.append(status),
    )
    assert b''.join(response)
    assert statuses == ['503 SERVICE UNAVAILABLE']
    finish.set()
    worker.join(2)
    assert not worker.is_alive()
    assert list(second) == [b'page']


def test_interrupted_switch_recovers_on_next_startup(site):
    config, _, _ = site
    paths = backup.data_paths(config)
    repository = paths['repository']
    old = repository.with_name('repository.site-old-test')
    new = backup._stage_path(repository)
    data = {
        'id': 'operation',
        'phase': 'switching',
        'moves': [
            {
                'target': str(repository),
                'old': str(old),
                'new': str(new),
                'existed': True,
            }
        ],
    }
    backup.write_journal(paths['maintenance'], data)
    backup.os.replace(repository, old)
    backup.apply_pending(config)
    assert (repository / 'home.md').exists()
    assert not old.exists()
    assert not new.exists()


def test_cli_export_and_confirmed_reset(site, tmp_path):
    config, _, _ = site
    settings = tmp_path / 'commands.cfg'
    settings.write_text(
        '\n'.join(
            f'{key} = {value!r}'
            for key, value in config.items()
            if key in backup.DEFAULT_CONFIG
        )
    )
    command = [
        'venv/bin/python',
        '-m',
        'otterwiki.site_backup',
        '--settings',
        str(settings),
    ]
    archive = tmp_path / 'cli-export.zip'
    result = subprocess.run(
        command + ['export', '--archive', str(archive)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert zipfile.is_zipfile(archive)
    if backup.os.name != 'nt':
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    result = subprocess.run(
        command + ['reset', '--confirm', 'RESET OTTERWIKI'],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with closing(
        backup._connect(backup.data_paths(config)['database'])
    ) as database:
        assert database.execute('SELECT count(*) FROM user').fetchone()[0] == 0
    assert not (Path(config['REPOSITORY']) / 'home.md').exists()
