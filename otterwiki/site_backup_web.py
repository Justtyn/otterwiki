# vim: set et ts=8 sts=4 sw=4 ai:

"""管理员备份入口；HTTP 只生成快照或暂存导入，数据替换在停站启动时进行。"""

from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import time

from flask import abort, render_template, request, send_file
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.wrappers import Response
from werkzeug.wsgi import ClosingIterator

from otterwiki.import_runtime import (
    ProcessLock,
    blocked,
    git_sync_root,
    set_site_maintenance,
    task_root,
    wait_for_readers,
)
from otterwiki.site_backup import (
    BackupError,
    backup_root,
    build_database,
    create_archive,
    data_paths,
    validate_archive,
)


class SiteGate:
    """覆盖完整 WSGI 响应生命周期，导出时等待其他请求退出。"""

    def __init__(self, wrapped, config):
        self.wrapped = wrapped
        self.config = config
        self.condition = threading.Condition()
        self.states = {}

    def state(self):
        key = str(backup_root(self.config))
        return self.states.setdefault(key, {'readers': 0, 'busy': False})

    def __call__(self, environ, start_response):
        with self.condition:
            state = self.state()
            if state['busy']:
                return Response(
                    '网站正在生成或校验备份，请稍后重试。',
                    status=503,
                    headers={'Retry-After': '10', 'Cache-Control': 'no-store'},
                    content_type='text/plain; charset=utf-8',
                )(environ, start_response)
            state['readers'] += 1

        def release():
            with self.condition:
                state['readers'] -= 1
                self.condition.notify_all()

        try:
            response = self.wrapped(environ, start_response)
        except BaseException:
            release()
            raise

        finished = False

        def finish():
            nonlocal finished
            if finished:
                return
            finished = True
            try:
                if hasattr(response, 'close'):
                    response.close()
            finally:
                release()

        def stream():
            try:
                yield from response
            finally:
                finish()

        return ClosingIterator(stream(), finish)

    @contextmanager
    def exclusive(self):
        with self.condition:
            state = self.state()
            if state['busy']:
                raise BackupError('已有整站备份操作正在执行。')
            state['busy'] = True
        try:
            deadline = time.monotonic() + 30
            with self.condition:
                while state['readers'] > 1:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BackupError('网站仍有请求正在处理，请稍后重试。')
                    self.condition.wait(remaining)
            yield
        finally:
            with self.condition:
                state['busy'] = False
                self.condition.notify_all()


@contextmanager
def snapshot_lock(app):
    from otterwiki.models import Space
    from otterwiki.server import db
    from otterwiki.spaces import space_repository_path

    try:
        import uwsgi

        if uwsgi.numproc > 1:
            raise BackupError(
                '整站备份要求单 Web 进程，请先设置 processes=1。'
            )
    except ImportError:
        pass
    locks = []
    root = task_root(app.config)
    with app.extensions['site_backup_gate'].exclusive():
        try:
            paths = []
            lock_roots = [backup_root(app.config), root]
            for space in Space.query.all():
                path = space_repository_path(space)
                if blocked(root, path):
                    raise BackupError('存在导入或同步恢复现场，请先完成恢复。')
                paths.append(path)
                lock_roots.append(git_sync_root(root) / f'space-{space.id}')
            for lock_root in lock_roots:
                lock = ProcessLock(lock_root)
                if not lock.acquire():
                    raise BackupError(
                        '文档导入、Git 同步或整站维护仍在执行，请稍后重试。'
                    )
                locks.append(lock)
            set_site_maintenance(root, True)
            for path in paths:
                wait_for_readers(path, timeout=30)
            # 先释放当前请求的读事务，再由 SQLite backup 创建一致性快照。
            db.session.remove()
            yield
        finally:
            set_site_maintenance(root, False)
            for lock in reversed(locks):
                lock.release()


def install_site_backup(app):
    from otterwiki.auth import has_permission
    from otterwiki.server import db

    gate = SiteGate(app.wsgi_app, app.config)
    app.wsgi_app = gate
    app.extensions['site_backup_gate'] = gate

    def page(error=None, success=None, status=200):
        response = Response(
            render_template(
                'admin/site_backup.html',
                title='网站备份与恢复',
                error=error,
                success=success,
                pending=(backup_root(app.config) / 'pending.zip').exists(),
                max_upload_mb=int(app.config['SITE_BACKUP_MAX_ARCHIVE_SIZE'])
                // (1024 * 1024),
            ),
            status=status,
            content_type='text/html; charset=utf-8',
        )
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/-/admin/site_backup', methods=['GET', 'POST'])
    @login_required
    def admin_site_backup():
        if not has_permission('ADMIN'):
            abort(403)
        if request.method == 'GET':
            return page()
        # 在解析 multipart 前限制请求总大小，包含少量表单边界开销。
        request.max_content_length = (
            int(app.config['SITE_BACKUP_MAX_ARCHIVE_SIZE']) + 1024 * 1024
        )
        try:
            action = request.form.get('action')
            if action not in ('export', 'import', 'cancel'):
                raise BackupError('请选择有效的备份操作。')
            if (
                action == 'import'
                and request.form.get('confirmation') != 'RESTORE OTTERWIKI'
            ):
                raise BackupError('请输入 RESTORE OTTERWIKI 确认覆盖网站。')
            root = backup_root(app.config)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            pending = root / 'pending.zip'
            with snapshot_lock(app):
                if action == 'cancel':
                    pending.unlink(missing_ok=True)
                    return page(
                        success='已取消待导入备份，现有网站数据未修改。'
                    )
                if action == 'export':
                    output = tempfile.TemporaryFile(mode='w+b')
                    try:
                        if db.engine.dialect.name != 'sqlite':
                            raise BackupError('整站备份仅支持 SQLite 数据库。')
                        connection = db.engine.raw_connection()
                        try:
                            create_archive(
                                app.config,
                                connection.driver_connection,
                                output,
                            )
                        finally:
                            connection.close()
                        output.seek(0)
                        response = send_file(
                            output,
                            mimetype='application/zip',
                            as_attachment=True,
                            download_name='otterwiki-backup-'
                            + datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
                            + '.zip',
                        )
                        response.headers['Cache-Control'] = 'no-store'
                        response.direct_passthrough = False
                        response.call_on_close(output.close)
                        return response
                    except BaseException:
                        output.close()
                        raise
                if pending.exists():
                    raise BackupError('已有待导入备份，请先取消后再上传。')
                paths = data_paths(app.config)
                upload = request.files.get('backup')
                if upload is None or not upload.filename:
                    raise BackupError('请选择本站导出的 ZIP 备份文件。')
                with tempfile.TemporaryDirectory(
                    prefix='upload-', dir=root
                ) as temporary:
                    temporary = Path(temporary)
                    archive = temporary / 'backup.zip'
                    upload.save(archive)
                    extracted = temporary / 'extracted'
                    extracted.mkdir()
                    manifest = validate_archive(app.config, archive, extracted)
                    # 上传时就验证目标结构、外键和凭据，避免重启后才发现不兼容。
                    build_database(
                        paths['database'],
                        extracted / 'database.sqlite',
                        temporary / 'verified.sqlite',
                        manifest['settings'],
                        manifest['repository_secrets'],
                        app.config['SECRET_KEY'],
                    )
                    archive.chmod(0o600)
                    os.replace(archive, pending)
            return page(
                success='备份已校验并等待导入。请停止所有实例后重新启动网站；启动时将覆盖现有数据。导入完成后使用备份中的管理员账号登录。'
            )
        except (BackupError, sqlite3.Error, SQLAlchemyError, OSError) as error:
            db.session.rollback()
            if not isinstance(error, BackupError):
                app.logger.exception('整站备份操作失败')
            return page(
                error=(
                    str(error)
                    if isinstance(error, BackupError)
                    else '备份操作失败，请检查磁盘空间、权限和数据库升级状态。'
                ),
                status=400,
            )

    # CSRF 在视图之前解析表单，因此上传限制也必须在 CSRF 钩子之前设置。
    @app.before_request
    def site_backup_upload_limit():
        if request.endpoint == 'admin_site_backup':
            request.max_content_length = (
                int(app.config['SITE_BACKUP_MAX_ARCHIVE_SIZE']) + 1024 * 1024
            )

    app.before_request_funcs[None].remove(site_backup_upload_limit)
    app.before_request_funcs[None].insert(0, site_backup_upload_limit)
