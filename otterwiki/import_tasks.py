#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""单进程、多请求线程下的持久化文档导入任务。"""

from dataclasses import asdict
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from flask import abort, jsonify, url_for, g
from flask_login import current_user

from otterwiki.import_runtime import (
    ProcessLock,
    blocked,
    journals,
    recover_journal,
    remove_journal,
    task_root,
    wait_for_readers,
    write_journal,
)

ACTIVE = ("queued", "running")
PHASES = {
    "queued": "等待执行",
    "waiting": "等待当前空间请求结束",
    "extracting": "解压压缩包",
    "scanning": "扫描文档",
    "converting": "转换文档与附件",
    "verifying": "校验导入内容",
    "git": "构建 Git 仓库",
    "switching": "替换文档仓库",
    "finishing": "清理草稿与刷新缓存",
    "succeeded": "文档仓库重建完成",
}


def _objects():
    from otterwiki.server import app, db
    from otterwiki.models import DocumentImportTask

    return app, db, DocumentImportTask


def _json(data, status=200):
    response = jsonify(data)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _admin_space():
    from otterwiki.auth import has_permission
    from otterwiki.spaces import current_space

    if not has_permission("ADMIN"):
        abort(403)
    space = current_space()
    if space is None:
        abort(404)
    return space


def serialize(task):
    now = time.time()
    return {
        "id": task.id,
        "status": task.status,
        "phase": task.phase,
        "phase_label": PHASES.get(task.phase, task.phase),
        "completed": task.completed,
        "total": task.total,
        "extracted_bytes": task.extracted_bytes,
        "elapsed_seconds": max(
            0, int((task.finished_at or now) - task.created_at)
        ),
        "updated_at": task.updated_at,
        "result": task.result,
        "error": task.error,
        "status_url": url_for("admin_document_import_task", task_id=task.id),
        "document_url": url_for("view", path="apstack6"),
        "maintenance": bool((task.checkpoint or {}).get("maintenance")),
    }


def recover_tasks(locked=False):
    """文件锁优先于心跳：即使心跳延迟，也不能抢占活任务。"""
    app, db, model = _objects()
    root = task_root(app.config)
    if not root.exists():
        return
    lock = None
    if not locked:
        lock = ProcessLock(root)
        if not lock.acquire():
            return
    try:
        for data in journals(root):
            task = db.session.get(model, data["id"])
            if data.get("_corrupt"):
                if task and task.status in ACTIVE:
                    task.status = "interrupted"
                    task.error = data["error"]
                    task.finished_at = task.updated_at = time.time()
                    task.checkpoint = data
                    db.session.commit()
                continue
            # 成功记录已经提交但进程在解除维护前退出。
            if task and task.status == "succeeded":
                if data.get("maintenance"):
                    data.update(status="succeeded", maintenance=False)
                    write_journal(root, data)
                remove_journal(root, task.id)
                continue
            if data.get("status") in ACTIVE or (
                data.get("status") == "interrupted" and data.get("maintenance")
            ):
                data = recover_journal(root, data)
            if task and task.status in ACTIVE:
                task.status = "interrupted"
                task.error = data.get("error", "任务已中断，请核对后重试。")
                task.finished_at = task.updated_at = time.time()
                task.checkpoint = data
                db.session.commit()
            elif task and task.status == "interrupted":
                task.error = data.get("error", task.error)
                task.checkpoint = data
                db.session.commit()
        # 兼容落库后、日志创建前中断的任务，不自动执行。
        for task in model.query.filter(model.status.in_(ACTIVE)).all():
            task.status = "interrupted"
            task.error = "任务已中断，未自动重跑，请核对后重新提交。"
            task.finished_at = task.updated_at = time.time()
        db.session.commit()
    finally:
        if lock:
            lock.release()


def latest_task():
    space = _admin_space()
    _, _, model = _objects()
    recover_tasks()
    task = (
        model.query.filter_by(space_id=space.id)
        .order_by(model.created_at.desc())
        .first()
    )
    return serialize(task) if task else None


def get_task(task_id):
    space = _admin_space()
    _, db, model = _objects()
    task = db.session.get(model, task_id)
    if task is None or task.space_id != space.id:
        abort(404)
    recover_tasks()
    db.session.refresh(task)
    return _json(serialize(task))


def submit_task(form, files):
    from otterwiki.auth import get_author
    from otterwiki.document_import import (
        CONFIRMATION_TEXT,
        DocumentImportError,
        _configured_limits,
        save_upload,
    )
    from otterwiki.spaces import current_storage

    space = _admin_space()
    if space.is_archived:
        return _json({"error": "归档空间不能导入文档。"}, 409)
    app, db, model = _objects()
    # 多个 uWSGI 进程各自持有仓库缓存及活动请求计数，不支持混用。
    try:
        import uwsgi

        if uwsgi.numproc > 1:
            return _json(
                {"error": "异步导入要求单 Web 进程，请设置 processes=1。"}, 503
            )
    except ImportError:
        pass
    if form.get("confirmation", "").strip() != CONFIRMATION_TEXT:
        return _json(
            {"error": f"请输入 {CONFIRMATION_TEXT} 以确认重置。"}, 400
        )
    key = form.get("request_key", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{16,64}", key):
        return _json({"error": "导入请求标识无效，请刷新页面后重试。"}, 400)
    user_id = current_user.id
    existing = model.query.filter_by(
        user_id=user_id, space_id=space.id, request_key=key
    ).first()
    if existing:
        return _json(serialize(existing), 202)
    directory = form.get("source_directory", "").strip()
    upload = files.get("archive")
    has_upload = bool(upload and upload.filename)
    if has_upload == bool(directory):
        return _json(
            {"error": "必须且只能选择一种来源：上传 ZIP 或服务器文件夹。"}, 400
        )
    if has_upload and not upload.filename.lower().endswith(".zip"):
        return _json({"error": "仅支持上传 .zip 压缩包。"}, 400)
    if directory and not Path(directory).expanduser().is_absolute():
        return _json({"error": "服务器文件夹必须填写绝对路径。"}, 400)
    root = task_root(app.config)
    lock = ProcessLock(root)
    if not lock.acquire():
        return _json({"error": "已有文档导入任务正在执行，请稍后再试。"}, 409)
    workspace = None
    task = None
    journal = None
    handed_off = False
    try:
        recover_tasks(locked=True)
        # 获得锁后重新检查幂等记录，防止同时提交在首轮查询中均未命中。
        existing = model.query.filter_by(
            user_id=user_id, space_id=space.id, request_key=key
        ).first()
        if existing:
            return _json(serialize(existing), 202)
        if any(j.get("maintenance") for j in journals(root)):
            return _json(
                {"error": "存在需要人工恢复的导入现场，请先处理。"}, 409
            )
        storage = current_storage()
        task_id = uuid.uuid4().hex
        target = Path(storage.path).resolve()
        workspace = target.parent / (".otterwiki-import-" + task_id)
        workspace.mkdir()
        source = (
            Path(directory).expanduser()
            if directory
            else workspace / "upload.zip"
        )
        limits = _configured_limits(app)
        if has_upload:
            save_upload(upload.stream, source, limits.max_archive_size)
        now = time.time()
        journal = {
            "id": task_id,
            "space_id": space.id,
            "target": str(target),
            "old_commit": storage.repo.head.commit.hexsha,
            "status": "queued",
            "phase": "queued",
            "maintenance": True,
            "workspace": str(workspace),
            "source_name": (
                Path(upload.filename.replace("\\", "/")).name
                if has_upload
                else source.name
            ),
        }
        # 维护标志与活动请求登记共享同步锁。
        write_journal(root, journal)
        task = model(
            id=task_id,
            user_id=user_id,
            space_id=space.id,
            request_key=key,
            status="queued",
            phase="queued",
            created_at=now,
            updated_at=now,
            workspace=str(workspace),
            checkpoint=journal,
        )
        db.session.add(task)
        db.session.commit()
        response = _json(serialize(task), 202)
        author = get_author()
        start_task(
            app,
            task_id,
            storage,
            source,
            has_upload,
            limits,
            author,
            lock,
            journal,
        )
        handed_off = True
        return response
    except DocumentImportError as error:
        return _json({"error": str(error)}, 400)
    except Exception:
        app.logger.exception("后台导入任务启动失败")
        db.session.rollback()
        if journal:
            journal.update(
                status="failed",
                maintenance=False,
                error="后台导入任务启动失败，请重试。",
            )
            write_journal(root, journal)
            remove_journal(root, journal["id"])
        if task:
            saved = db.session.get(model, task.id)
            if saved:
                saved.status = "failed"
                saved.error = "后台导入任务启动失败，请重试。"
                saved.finished_at = saved.updated_at = time.time()
                saved.checkpoint = journal
                db.session.commit()
        return _json({"error": "后台导入任务启动失败，请查看服务日志。"}, 500)
    finally:
        if not handed_off:
            if workspace:
                shutil.rmtree(workspace, ignore_errors=True)
            lock.release()


def start_task(
    app, task_id, storage, source, is_zip, limits, author, lock, journal
):
    thread = threading.Thread(
        target=run_task,
        name="document-import-" + task_id,
        args=(
            app,
            task_id,
            storage,
            source,
            is_zip,
            limits,
            author,
            lock,
            journal,
        ),
        daemon=True,
    )
    thread.start()


class Progress:
    def __init__(self, app, task_id):
        self.app = app
        self.task_id = task_id
        self.last_write = 0
        self.phase = None

    def __call__(self, phase, completed=0, total=None, extracted_bytes=0):
        now = time.monotonic()
        if (
            phase == self.phase
            and now - self.last_write < 1
            and completed != total
        ):
            return
        self.phase, self.last_write = phase, now
        _, db, model = _objects()
        task = db.session.get(model, self.task_id)
        task.phase = phase
        task.completed = completed
        task.total = total
        task.extracted_bytes = extracted_bytes
        task.updated_at = time.time()
        db.session.commit()


def _heartbeat(app, task_id, stop):
    while not stop.wait(5):
        with app.app_context():
            _, db, model = _objects()
            try:
                model.query.filter_by(id=task_id).filter(
                    model.status.in_(ACTIVE)
                ).update({"updated_at": time.time()})
                db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception("更新导入任务心跳失败")


def run_task(
    app, task_id, storage, source, is_zip, limits, author, lock, journal
):
    from otterwiki.document_import import (
        DocumentImportError,
        extract_zip_safely,
        reset_repository_from_source,
        _remove_tree_with_retries,
    )
    from otterwiki.models import Space, Drafts, User

    root = task_root(app.config)
    workspace = Path(journal["workspace"])
    stop = threading.Event()
    heartbeat = None
    keep_workspace = False
    try:
        with app.app_context():
            _, db, model = _objects()
            task = db.session.get(model, task_id)
            task.status = "running"
            task.started_at = task.updated_at = time.time()
            db.session.commit()
            journal["status"] = "running"
            write_journal(root, journal)
            heartbeat = threading.Thread(
                target=_heartbeat, args=(app, task_id, stop), daemon=True
            )
            heartbeat.start()
            progress = Progress(app, task_id)

            def check_space():
                db.session.expire_all()
                space = db.session.get(Space, journal["space_id"])
                user = db.session.get(User, task.user_id)
                if (
                    space is None
                    or space.is_archived
                    or user is None
                    or not user.is_admin
                ):
                    raise DocumentImportError(
                        "空间或管理员授权已失效，未执行仓库替换。"
                    )
                g.space = space
                db.session.commit()

            def checkpoint(**values):
                check_space()
                journal.update(values, phase="switching")
                write_journal(root, journal)
                progress("switching")
                task.checkpoint = dict(journal)
                db.session.commit()

            check_space()
            progress("waiting")
            wait_for_readers(storage.path)
            if is_zip:
                extracted = workspace / "extracted"
                extracted.mkdir()
                extract_zip_safely(source, extracted, limits, progress)
                source = extracted
            progress("scanning")
            result = reset_repository_from_source(
                source,
                storage,
                limits=limits,
                author=author,
                progress=progress,
                workspace=workspace,
                checkpoint=checkpoint,
            )
            if is_zip:
                result.source_name = journal["source_name"]
            journal["phase"] = "finishing"
            write_journal(root, journal)
            progress("finishing")
            try:
                Drafts.query.filter_by(space_id=journal["space_id"]).delete()
                db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception("导入完成后清理草稿失败")
                result.warnings.append(
                    "文档已导入，但旧草稿未能清理，请在维护工具中检查。"
                )
            task = db.session.get(model, task_id)
            task.result = asdict(result)
            task.status = task.phase = "succeeded"
            task.finished_at = task.updated_at = time.time()
            task.checkpoint = dict(
                journal, maintenance=False, status="succeeded"
            )
            db.session.commit()
            # 成功落库后才能解除维护和清理旧仓库。
            journal.update(
                status="succeeded", phase="succeeded", maintenance=False
            )
            write_journal(root, journal)
            if journal.get("backup"):
                warnings = []
                try:
                    _remove_tree_with_retries(Path(journal["backup"]))
                except OSError as error:
                    warnings.append(str(error))
                if warnings:
                    result.warnings.append(
                        "旧仓库备份清理未完成，请查看服务日志。"
                    )
                    app.logger.warning("导入备份清理提示：%s", warnings)
                    task.result = asdict(result)
                    db.session.commit()
    except BaseException as error:
        keep_workspace = journal.get("phase") in (
            "switching",
            "finishing",
            "succeeded",
        )
        app.logger.exception("文档导入任务 %s 未完整结束", task_id)
        with app.app_context():
            _, db, model = _objects()
            db.session.rollback()
            if keep_workspace:
                journal = recover_journal(root, journal)
                try:
                    storage.repo.close()
                except Exception:
                    pass
                if not journal["maintenance"]:
                    storage.repo = storage._read_repo()
            else:
                message = (
                    str(error)
                    if isinstance(error, (DocumentImportError, TimeoutError))
                    else "导入失败，原仓库未替换，请查看服务日志。"
                )
                message = message.replace(
                    str(workspace), "任务临时目录"
                ).replace(str(storage.path), "当前仓库")
                message = message.replace(str(source), "导入源")
                journal.update(
                    status="failed", maintenance=False, error=message
                )
                write_journal(root, journal)
            task = db.session.get(model, task_id)
            if task and task.status != "succeeded":
                task.status = journal["status"]
                task.error = journal.get("error")
                task.finished_at = task.updated_at = time.time()
                task.checkpoint = journal
                db.session.commit()
    finally:
        stop.set()
        if heartbeat and heartbeat.ident:
            heartbeat.join(timeout=10)
        if not keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
            remove_journal(root, task_id)
        lock.release()


def install_request_guards(app):
    """按仓库跟踪请求，流式附件直到关闭响应才释放文件使用权。"""
    from flask import request
    from otterwiki.import_runtime import enter_repository

    @app.before_request
    def import_maintenance_gate():
        from otterwiki.spaces import (
            current_space,
            space_repository_path,
            SPACE_EXEMPT_ENDPOINTS,
        )
        from otterwiki.auth import has_permission

        endpoint = request.endpoint or ""
        safe_admin = endpoint.startswith("admin") and endpoint not in (
            "admin_repository_management",
            "admin_navigation",
            "admin_space_edit",
            "admin_space_archive",
            "admin_space_restore",
        )
        git_endpoint = endpoint in (
            "git_info_refs",
            "git_upload_pack",
            "git_receive_pack",
        )
        if endpoint == "git_info_refs" and request.args.get("service") not in (
            "git-upload-pack",
            "git-receive-pack",
        ):
            # 保留路由原有的 400 语义；无效请求不会打开或读写仓库。
            return
        if not endpoint or endpoint.startswith("interface_") or safe_admin:
            return
        if endpoint in SPACE_EXEMPT_ENDPOINTS and not (
            git_endpoint or endpoint == "pull_webhook"
        ):
            return
        if endpoint.startswith("admin") and not has_permission("ADMIN"):
            abort(403)
        space = current_space()
        if endpoint in (
            "admin_space_edit",
            "admin_space_archive",
            "admin_space_restore",
        ):
            from otterwiki.models import Space
            from otterwiki.server import db

            space = db.session.get(
                Space, (request.view_args or {}).get("space_id")
            )
        if space is None:
            return
        if git_endpoint:
            from otterwiki.remote import GitHttpServer

            # 在构造 GitHttpServer（会写 Git 配置）之前完成认证与维护检查。
            server = object.__new__(GitHttpServer)
            server.check_if_enabled()
            server.check_permission(
                "UPLOAD" if endpoint == "git_receive_pack" else "READ"
            )
        root = task_root(app.config)
        target = space_repository_path(space)
        release = enter_repository(root, target)
        if release is None:
            return _json({"error": "当前空间正在导入维护，请稍后访问。"}, 423)
        request.environ["otterwiki.import_release"] = release

    @app.after_request
    def finish_import_request(response):
        release = request.environ.pop("otterwiki.import_release", None)
        if release:
            if response.is_streamed:
                from werkzeug.wsgi import ClosingIterator

                response.response = ClosingIterator(response.response, release)
                response.call_on_close(release)
            else:
                release()
        return response

    @app.teardown_request
    def failed_import_request(error):
        release = request.environ.pop("otterwiki.import_release", None)
        if release:
            release()
