# vim: set et ts=8 sts=4 sw=4 ai:

"""整站备份格式与停站恢复，不导入 Web 应用，也不执行备份中的代码。"""

import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
import zipfile

from sqlalchemy.engine import make_url

from otterwiki.defaults import DEFAULT_CONFIG
from otterwiki.import_runtime import ProcessLock, task_root, write_journal

FORMAT = 'otterwiki-site-backup'
VERSION = 1
# 运行参数由目标部署决定，不能让上传内容改写路径、密钥或 Python 配置。
RUNTIME_KEYS = {
    'DEBUG',
    'TESTING',
    'LOG_LEVEL',
    'LOG_LEVEL_WERKZEUG',
    'REPOSITORY',
    'SECRET_KEY',
    'SQLALCHEMY_DATABASE_URI',
    'SQLALCHEMY_TRACK_MODIFICATIONS',
    'SPACES_ROOT',
    'DOCUMENT_IMPORT_TASK_ROOT',
    'SITE_BACKUP_ROOT',
    'MAX_FORM_MEMORY_SIZE',
    'SESSION_COOKIE_SAMESITE',
    'SECURITY_HEADERS',
    'WTF_CSRF_ENABLED',
    'WTF_CSRF_TIME_LIMIT',
}
SITE_KEYS = frozenset(
    key
    for key in DEFAULT_CONFIG
    if key not in RUNTIME_KEYS
    and not key.startswith(('APSTACK_IMPORT_', 'SITE_BACKUP_'))
)
SESSION_KEY = 'SITE_SESSION_GENERATION'
TRANSIENT_TABLES = ('cache', 'document_import_task', 'git_sync_task')
GIT_FILES = {
    'HEAD',
    'index',
    'description',
    'objects',
    'refs',
    'logs',
    'packed-refs',
    'shallow',
    'info',
}


class BackupError(ValueError):
    """可向管理员展示的校验或维护错误。"""


def backup_root(config):
    return Path(
        config.get('SITE_BACKUP_ROOT')
        or (Path(config['REPOSITORY']).resolve().parent / '.otterwiki-site')
    ).resolve()


def data_paths(config):
    url = make_url(config['SQLALCHEMY_DATABASE_URI'])
    if (
        url.drivername != 'sqlite'
        or not url.database
        or url.database == ':memory:'
    ):
        raise BackupError('整站恢复仅支持使用文件数据库的 SQLite 部署。')
    database = Path(url.database)
    if not database.is_absolute():
        raise BackupError('请先将数据库配置改为绝对路径，再执行整站维护。')
    repository = Path(config['REPOSITORY']).absolute()
    paths = {
        'database': database,
        'repository': repository,
        'spaces': Path(
            config.get('SPACES_ROOT') or repository.parent / 'spaces'
        ).absolute(),
        'tasks': task_root(config).absolute(),
        'maintenance': backup_root(config),
    }
    for path in paths.values():
        if path == Path(path.anchor) or path.is_symlink():
            raise BackupError('维护路径不能是根目录或符号链接。')
        if path.resolve() != path:
            raise BackupError('维护路径必须规范化，且不能经过符号链接。')
    for key, left in paths.items():
        for other, right in paths.items():
            if key != other and (left == right or left in right.parents):
                raise BackupError('数据库、仓库、空间及任务目录不能互相包含。')
    return paths


def _connect(path):
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def _schema(connection):
    entries = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    if any(kind not in ('table', 'index') for kind, _, _ in entries):
        raise BackupError('数据库包含不支持的视图或触发器。')
    return entries


def _table_names(connection):
    return [name for kind, name, _ in _schema(connection) if kind == 'table']


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _schema_signature(connection):
    """比较字段和约束，兼容前向迁移追加字段导致的排列差异。"""
    result = {}
    for table in _table_names(connection):
        columns = sorted(
            tuple(row[1:])
            for row in connection.execute(
                'PRAGMA table_info(' + _quote(table) + ')'
            )
        )
        indexes = set()
        for index in connection.execute(
            'PRAGMA index_list(' + _quote(table) + ')'
        ):
            names = tuple(
                row[2]
                for row in connection.execute(
                    'PRAGMA index_info(' + _quote(index[1]) + ')'
                )
            )
            indexes.add((index[2], names, index[4]))
        foreign_keys = sorted(
            tuple(row[1:])
            for row in connection.execute(
                'PRAGMA foreign_key_list(' + _quote(table) + ')'
            )
        )
        result[table] = (columns, indexes, foreign_keys)
    return result


def _setting(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'True' if value else 'False'
    return str(value)


def _cipher(key):
    from cryptography.fernet import Fernet

    material = ('otterwiki-space-git-v1\0' + str(key)).encode('utf-8')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def _git(path, *args):
    # 不读取宿主用户的 Git 配置，不允许仓库钩子和对象替换。
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith('GIT_')
    }
    env.update(
        GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_NO_REPLACE_OBJECTS='1',
        GIT_TERMINAL_PROMPT='0',
    )
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(path), *args],
        env=env,
        capture_output=True,
        timeout=180,
    )
    if result.returncode:
        raise BackupError('备份中的 Git 仓库校验失败，未修改网站数据。')


def _repo_files(repository):
    if (
        not (repository / '.git').is_dir()
        or (repository / '.git').is_symlink()
    ):
        raise BackupError('仅支持普通 Git 仓库，不支持工作树或符号链接。')
    for path in repository.rglob('*'):
        relative = path.relative_to(repository)
        if path.is_symlink():
            raise BackupError('仓库包含符号链接，请处理后再备份。')
        parts = relative.parts
        if parts[0] == '.git':
            if relative.as_posix() in (
                '.git/objects/info/alternates',
                '.git/objects/info/http-alternates',
                '.git/info/grafts',
            ):
                raise BackupError('仓库引用了外部对象，无法制作独立备份。')
            if len(parts) > 1 and parts[1] not in GIT_FILES:
                continue
        elif any(part.lower() == '.git' for part in parts):
            raise BackupError('不支持仓库内嵌套的 Git 目录。')
        if path.is_file():
            yield path, relative.as_posix()
        elif not path.is_dir():
            raise BackupError('仓库包含不支持的特殊文件。')


def create_archive(config, connection, destination):
    """调用方须已停站或持有整站门禁及所有后台任务锁。"""
    with tempfile.TemporaryDirectory(prefix='otterwiki-backup-') as temporary:
        snapshot = Path(temporary) / 'database.sqlite'
        with closing(sqlite3.connect(snapshot)) as copied:
            connection.backup(copied)
            copied.execute('PRAGMA journal_mode=DELETE')
            tables = _table_names(copied)
            if not {
                'space',
                'preferences',
                'space_git_repository',
                'schema_version',
            } <= set(tables):
                raise BackupError('数据库结构不完整，请先执行数据库升级。')
            for table in TRANSIENT_TABLES:
                copied.execute('DELETE FROM ' + _quote(table))
            preferences = dict(
                copied.execute('SELECT name, value FROM preferences')
            )
            copied.execute(
                'DELETE FROM preferences WHERE name NOT IN ('
                + ','.join('?' for _ in SITE_KEYS)
                + ')',
                tuple(SITE_KEYS),
            )
            settings = {
                key: config.get(key, DEFAULT_CONFIG[key]) for key in SITE_KEYS
            }
            for key, value in preferences.items():
                if key in SITE_KEYS:
                    settings[key] = value
            secrets = {}
            for record_id, encrypted in copied.execute(
                'SELECT id, secret_ciphertext FROM space_git_repository'
            ):
                if encrypted:
                    try:
                        secrets[str(record_id)] = (
                            _cipher(config['SECRET_KEY'])
                            .decrypt(encrypted.encode('ascii'))
                            .decode('utf-8')
                        )
                    except Exception as error:
                        raise BackupError(
                            '仓库凭据无法解密，请重新录入凭据后再备份。'
                        ) from error
            spaces = [
                dict(zip(('id', 'is_default'), row))
                for row in copied.execute(
                    'SELECT id, is_default FROM space ORDER BY id'
                )
            ]
            copied.commit()
        manifest = {
            'format': FORMAT,
            'version': VERSION,
            'settings': settings,
            'repository_secrets': secrets,
            'spaces': spaces,
        }
        root = Path(
            config.get('SPACES_ROOT')
            or Path(config['REPOSITORY']).resolve().parent / 'spaces'
        )
        with zipfile.ZipFile(
            destination, 'w', zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            archive.writestr(
                'manifest.json', json.dumps(manifest, ensure_ascii=False)
            )
            archive.write(snapshot, 'database.sqlite')
            for space in spaces:
                repository = (
                    Path(config['REPOSITORY'])
                    if space['is_default']
                    else root / str(space['id']) / 'repository'
                )
                # 未访问过的新空间可以没有实体目录，按空仓库导出。
                if not repository.exists() and not space['is_default']:
                    empty = Path(temporary) / str(space['id'])
                    empty.mkdir()
                    _git(empty, 'init', '-b', 'main')
                    repository = empty
                for path, relative in _repo_files(repository):
                    archive.write(
                        path, f"repositories/{space['id']}/{relative}"
                    )


def _extract(config, archive_path, destination):
    if archive_path.stat().st_size > int(
        config['SITE_BACKUP_MAX_ARCHIVE_SIZE']
    ):
        raise BackupError('备份文件超过上传大小限制。')
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > int(config['SITE_BACKUP_MAX_FILES']):
                raise BackupError('备份文件数量超过限制。')
            size = 0
            seen = set()
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                mode = member.external_attr >> 16
                size += member.file_size
                if size > int(config['SITE_BACKUP_MAX_EXTRACTED_SIZE']):
                    raise BackupError('备份解压大小超过限制。')
                if (
                    not parts
                    or '\\' in name
                    or ':' in name
                    or '\0' in name
                    or name.startswith('/')
                    or any(p in ('.', '..') for p in name.split('/'))
                    or name.rstrip('/').casefold() in seen
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or member.flag_bits & 1
                ):
                    raise BackupError(
                        '备份包含重复路径、不安全路径或特殊文件。'
                    )
                seen.add(name.rstrip('/').casefold())
                if name not in ('manifest.json', 'database.sqlite'):
                    if (
                        len(parts) < 3
                        or parts[0] != 'repositories'
                        or not re.fullmatch(r'[1-9][0-9]*', parts[1])
                    ):
                        raise BackupError('备份中存在格式之外的文件。')
                    tail = parts[2:]
                    if '.git' in [p.lower() for p in tail[1:]]:
                        raise BackupError('备份中存在嵌套的 Git 目录。')
                    if tail[0].lower() == '.git':
                        if (
                            tail[0] != '.git'
                            or len(tail) < 2
                            or tail[1] not in GIT_FILES
                        ):
                            raise BackupError(
                                '备份包含不支持的 Git 配置或钩子。'
                            )
                        if tail in (
                            ('.git', 'objects', 'info', 'alternates'),
                            ('.git', 'objects', 'info', 'http-alternates'),
                            ('.git', 'info', 'grafts'),
                        ):
                            raise BackupError('备份不能引用外部 Git 对象。')
                if member.is_dir():
                    continue
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    archive.open(member) as source,
                    target.open('xb') as output,
                ):
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise BackupError('备份文件损坏或无法解压。') from error


def validate_archive(config, archive_path, destination):
    """完整解压并校验；成功前不触碰任何现有网站数据。"""
    _extract(config, Path(archive_path), destination)
    try:
        manifest_path = destination / 'manifest.json'
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise BackupError('备份清单过大。')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get('settings'), dict)
            or not isinstance(manifest.get('repository_secrets'), dict)
            or not isinstance(manifest.get('spaces'), list)
        ):
            raise BackupError('备份清单格式无效。')
        if manifest['format'] != FORMAT or manifest['version'] != VERSION:
            raise BackupError('不支持此备份格式或版本。')
        if set(manifest['settings']) != SITE_KEYS:
            raise BackupError('备份的网站配置版本不匹配，请使用相同版本导入。')
        if not all(
            isinstance(v, (str, int, float, bool, type(None)))
            for v in manifest['settings'].values()
        ):
            raise BackupError('备份的网站配置格式无效。')
        with closing(_connect(destination / 'database.sqlite')) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('备份数据库完整性检查失败。')
            _schema(source)
            spaces = [
                dict(zip(('id', 'is_default'), row))
                for row in source.execute(
                    'SELECT id, is_default FROM space ORDER BY id'
                )
            ]
            for space_id, slug, is_default in source.execute(
                'SELECT id, slug, is_default FROM space'
            ):
                if (
                    type(space_id) is not int
                    or space_id < 1
                    or not isinstance(slug, str)
                    or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug)
                    or is_default not in (0, 1)
                    or (slug == 'default') != bool(is_default)
                ):
                    raise BackupError('备份的空间标识无效。')
            if (
                spaces != manifest['spaces']
                or sum(bool(s['is_default']) for s in spaces) != 1
            ):
                raise BackupError('备份的空间清单与数据库不一致。')
            if not source.execute(
                'SELECT 1 FROM user WHERE is_admin=1'
            ).fetchone():
                raise BackupError('备份中没有管理员账号，无法恢复。')
            secrets = manifest['repository_secrets']
            expected = {
                str(row[0])
                for row in source.execute(
                    "SELECT id FROM space_git_repository WHERE secret_ciphertext IS NOT NULL AND secret_ciphertext != ''"
                )
            }
            if set(secrets) != expected or not all(
                isinstance(v, str) for v in secrets.values()
            ):
                raise BackupError('备份的仓库凭据清单无效。')
            for table in TRANSIENT_TABLES:
                if source.execute(
                    'SELECT COUNT(*) FROM ' + _quote(table)
                ).fetchone()[0]:
                    raise BackupError('备份不能包含任务现场或缓存。')
        expected_dirs = {str(s['id']) for s in spaces}
        if {
            p.name for p in (destination / 'repositories').iterdir()
        } != expected_dirs:
            raise BackupError('备份仓库与空间清单不一致。')
        for space in spaces:
            if type(space['id']) is not int or space['id'] < 1:
                raise BackupError('备份空间编号无效。')
            repository = destination / 'repositories' / str(space['id'])
            if not (repository / '.git' / 'HEAD').is_file():
                raise BackupError('备份缺少 Git 仓库信息。')
            for name in ('objects', 'refs'):
                (repository / '.git' / name).mkdir(exist_ok=True)
            # 使用全新配置，绝不还原可能执行程序的 Git 配置或钩子。
            (repository / '.git' / 'config').write_text(
                '[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tlogallrefupdates = true\n',
                encoding='utf-8',
            )
            _git(repository, 'fsck', '--full', '--no-reflogs')
        return manifest
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ) as error:
        if isinstance(error, BackupError):
            raise
        raise BackupError(
            '备份内容不完整或格式无效，未修改网站数据。'
        ) from error


def build_database(
    template, source_path, output, settings, secrets, secret_key
):
    """只使用目标数据库的可信建表语句，并通过参数化插入恢复数据。"""
    with (
        closing(_connect(template)) as reference,
        closing(sqlite3.connect(output)) as target,
    ):
        schema = _schema(reference)
        for kind, _, sql in schema:
            if kind == 'table' and sql:
                target.execute(sql)
        for kind, _, sql in schema:
            if kind == 'index' and sql:
                target.execute(sql)
        tables = _table_names(reference)
        target.execute('PRAGMA foreign_keys=ON')
        target.execute('BEGIN')
        target.execute('PRAGMA defer_foreign_keys=ON')
        with closing(_connect(source_path or template)) as source:
            if _schema_signature(source) != _schema_signature(reference):
                raise BackupError(
                    '备份与当前数据库结构不同，请使用相同版本并完成数据库升级。'
                )
            if (
                source.execute(
                    'SELECT version FROM schema_version ORDER BY version'
                ).fetchall()
                != reference.execute(
                    'SELECT version FROM schema_version ORDER BY version'
                ).fetchall()
            ):
                raise BackupError('数据库迁移版本不匹配，请先对齐版本。')
            for table in tables:
                if table in TRANSIENT_TABLES or table == 'preferences':
                    continue
                if not source_path and table != 'schema_version':
                    continue
                cursor = source.execute('SELECT * FROM ' + _quote(table))
                names = [col[0] for col in cursor.description]
                sql = (
                    'INSERT INTO '
                    + _quote(table)
                    + ' ('
                    + ','.join(map(_quote, names))
                    + ') VALUES ('
                    + ','.join('?' for _ in names)
                    + ')'
                )
                target.executemany(sql, cursor)
        for key in SITE_KEYS:
            target.execute(
                'INSERT INTO preferences (name, value) VALUES (?, ?)',
                (key, _setting(settings[key])),
            )
        # 每次恢复生成新的会话 Cookie 名，避免旧用户 ID 被复用后继承权限。
        target.execute(
            'INSERT INTO preferences (name, value) VALUES (?, ?)',
            (SESSION_KEY, uuid.uuid4().hex),
        )
        for record_id, secret in secrets.items():
            target.execute(
                'UPDATE space_git_repository SET secret_ciphertext=? WHERE id=?',
                (
                    _cipher(secret_key)
                    .encrypt(secret.encode('utf-8'))
                    .decode('ascii'),
                    int(record_id),
                ),
            )
        target.execute('UPDATE space_git_repository SET auto_push_pending=0')
        if target.execute('PRAGMA foreign_key_check').fetchone():
            raise BackupError('备份的账号、空间或授权关系不完整。')
        target.commit()
        target.execute('PRAGMA journal_mode=DELETE')
    output.chmod(0o600)


def _remove(path):
    if path.is_dir() and not path.is_symlink():

        def writable_retry(function, filename, _error):
            os.chmod(filename, os.stat(filename).st_mode | stat.S_IWRITE)
            function(filename)

        shutil.rmtree(path, onerror=writable_retry)
    else:
        path.unlink(missing_ok=True)


def recover_operation(config):
    """停站启动时恢复未完成切换；失败时保留日志，禁止继续启动。"""
    root = backup_root(config)
    journal = root / 'operation.json'
    if not journal.exists():
        return
    data = json.loads(journal.read_text(encoding='utf-8'))
    paths = data_paths(config)
    for item in reversed(data['moves']):
        target, old, new = (
            Path(item[key]) for key in ('target', 'old', 'new')
        )
        if (
            target not in paths.values()
            or old.parent != target.parent
            or new.parent != target.parent
        ):
            raise BackupError(
                '维护日志路径与配置不一致，请保持停站并人工检查。'
            )
        if data['phase'] != 'done':
            if old.exists():
                _remove(target)
                os.replace(old, target)
            elif not item['existed'] and not new.exists():
                _remove(target)
        else:
            _remove(old)
        _remove(new)
    journal.unlink()


def _switch(config, staged):
    root = backup_root(config)
    data = {'id': 'operation', 'phase': 'switching', 'moves': []}
    for target, new in staged:
        data['moves'].append(
            {
                'target': str(target),
                'new': str(new),
                'old': str(
                    target.with_name(
                        target.name + '.site-old-' + uuid.uuid4().hex
                    )
                ),
                'existed': target.exists(),
            }
        )
    write_journal(root, data)
    try:
        for item in data['moves']:
            if item['existed']:
                os.replace(item['target'], item['old'])
            os.replace(item['new'], item['target'])
        data['phase'] = 'done'
        write_journal(root, data)
    except BaseException:
        recover_operation(config)
        raise
    recover_operation(config)


def _stage_path(target, directory=True):
    target.parent.mkdir(parents=True, exist_ok=True)
    path = Path(
        tempfile.mkdtemp(prefix=target.name + '.site-new-', dir=target.parent)
    )
    if not directory:
        path.rmdir()
    return path


def _inherit_owner(target, staged):
    """维护命令以 root 运行时，保持 Web 进程原有的卷访问身份。"""
    if os.name == 'nt' or not hasattr(os, 'chown'):
        return
    owner = (target if target.exists() else target.parent).stat()
    paths = [staged, *staged.rglob('*')] if staged.is_dir() else [staged]
    for path in paths:
        if (
            path.stat().st_uid != owner.st_uid
            or path.stat().st_gid != owner.st_gid
        ):
            os.chown(path, owner.st_uid, owner.st_gid)


def restore_site(config, archive=None):
    """仅限停站调用；archive 为空表示清空内容并恢复出厂网站设置。"""
    paths = data_paths(config)
    root = paths['maintenance']
    if not paths['database'].is_file():
        raise BackupError('数据库文件不存在，请检查配置路径。')
    pending = root / 'pending.zip'
    if pending.exists() and (
        archive is None or Path(archive).resolve() != pending
    ):
        raise BackupError('已有待导入备份，请先取消待导入任务。')
    lock = ProcessLock(root)
    if not lock.acquire():
        raise BackupError('已有整站维护操作正在执行。')
    staged = []
    try:
        _inherit_owner(paths['repository'], root)
        recover_operation(config)
        # 将 WAL 合并并关闭连接后才能安全移动数据库文件。
        with closing(sqlite3.connect(paths['database'])) as database:
            if database.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[
                0
            ]:
                raise BackupError('数据库仍被占用，请先停止所有网站进程。')
            database.execute('PRAGMA journal_mode=DELETE')
        with tempfile.TemporaryDirectory(
            prefix='validated-', dir=root
        ) as temporary:
            extracted = Path(temporary)
            manifest = (
                validate_archive(config, Path(archive), extracted)
                if archive
                else None
            )
            settings = manifest['settings'] if manifest else DEFAULT_CONFIG
            for name in ('database', 'repository', 'spaces', 'tasks'):
                staged.append(
                    (paths[name], _stage_path(paths[name], name != 'database'))
                )
            new_database, repository, spaces, tasks = [
                new for _, new in staged
            ]
            build_database(
                paths['database'],
                extracted / 'database.sqlite' if archive else None,
                new_database,
                settings,
                manifest['repository_secrets'] if manifest else {},
                config['SECRET_KEY'],
            )
            if manifest:
                for space in manifest['spaces']:
                    target = (
                        repository
                        if space['is_default']
                        else spaces / str(space['id']) / 'repository'
                    )
                    shutil.copytree(
                        extracted / 'repositories' / str(space['id']),
                        target,
                        dirs_exist_ok=True,
                    )
            else:
                _git(repository, 'init', '-b', 'main')
            for target, new in staged:
                _inherit_owner(target, new)
            _switch(config, staged)
    finally:
        # 回滚不成功时保留所有切换现场，留给下次启动恢复。
        if not (root / 'operation.json').exists():
            for _, new in staged:
                _remove(new)
        lock.release()


def apply_pending(config):
    """应用加载前执行；待导入失败时阻止启动，避免暴露半份站点。"""
    if not config.get('REPOSITORY'):
        return
    root = backup_root(config)
    pending = root / 'pending.zip'
    if (root / 'operation.json').exists():
        lock = ProcessLock(root)
        if not lock.acquire():
            raise BackupError('整站维护仍在执行，暂时不能启动。')
        try:
            recover_operation(config)
        finally:
            lock.release()
    if pending.exists():
        restore_site(config, pending)
        pending.unlink()


def load_config(settings):
    """仅加载管理员指定的本地配置文件，环境变量规则与 Web 启动一致。"""
    from flask.config import Config

    config = Config(os.getcwd(), DEFAULT_CONFIG)
    config.from_pyfile(str(Path(settings).resolve()))
    for key in list(config):
        value = os.environ.get(key)
        if value is not None:
            config[key] = (
                value.lower() in ('true', 'yes', 'on', '1')
                if isinstance(config[key], bool)
                else value
            )
    return config


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='整站备份与初始化；恢复和重置前必须停止所有网站进程。'
    )
    parser.add_argument(
        '--settings',
        default=os.environ.get('OTTERWIKI_SETTINGS', 'settings.cfg'),
        help='本地配置文件',
    )
    parser.add_argument(
        'action', choices=('reset', 'restore', 'apply-pending', 'export')
    )
    parser.add_argument('--archive', help='导入或导出的备份 ZIP 路径')
    parser.add_argument(
        '--confirm', help='重置填 RESET OTTERWIKI；导入填 RESTORE OTTERWIKI'
    )
    args = parser.parse_args()
    try:
        config = load_config(args.settings)
        paths = data_paths(config)
        if args.action == 'reset' and args.confirm != 'RESET OTTERWIKI':
            print('尚未执行。停站并备份后，添加 --confirm "RESET OTTERWIKI"。')
            for name, path in paths.items():
                print(f'{name}: {path}')
            return 0
        if (
            args.action in ('restore', 'apply-pending')
            and args.confirm != 'RESTORE OTTERWIKI'
        ):
            raise BackupError(
                '请先停站，并添加 --confirm "RESTORE OTTERWIKI"。'
            )
        if args.action == 'export':
            if not args.archive:
                raise BackupError('请通过 --archive 指定导出文件。')
            descriptor = os.open(
                args.archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with (
                os.fdopen(descriptor, 'wb') as output,
                closing(_connect(paths['database'])) as connection,
            ):
                create_archive(config, connection, output)
        elif args.action == 'apply-pending':
            if not (backup_root(config) / 'pending.zip').exists():
                raise BackupError('没有待导入的备份。')
            apply_pending(config)
        elif args.action == 'restore':
            if not args.archive:
                raise BackupError('请通过 --archive 指定导入文件。')
            restore_site(config, args.archive)
        else:
            if (backup_root(config) / 'pending.zip').exists():
                raise BackupError(
                    '存在待导入备份，请先在管理页取消导入，再执行初始化。'
                )
            restore_site(config)
        print('操作完成。导入或初始化后，请重新启动网站。')
        return 0
    except (BackupError, OSError, sqlite3.Error) as error:
        print('操作未完成：' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
