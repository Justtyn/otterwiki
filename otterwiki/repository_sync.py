"""Per-space remote Git configuration and asynchronous synchronization."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import stat
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

import git
from flask import abort, jsonify, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, OperationalError

from otterwiki.auth import has_permission
from otterwiki.credentials import (
    CredentialError,
    decrypt_secret,
    encrypt_secret,
)
from otterwiki.helper import toast
from otterwiki.import_runtime import (
    ProcessLock,
    blocked,
    git_sync_root,
    journals,
    recover_journal,
    remove_journal,
    task_root,
    wait_for_readers,
    write_journal,
)
from otterwiki.models import Drafts, GitSyncTask, Space, SpaceGitRepository
from otterwiki.server import app, db
from otterwiki.spaces import get_space_storage, space_repository_path

ACTIVE = ("queued", "running")
OPERATIONS = ("import", "pull", "push")
PHASE_LABELS = {
    "queued": "等待执行",
    "connecting": "连接远程仓库",
    "cloning": "克隆指定分支",
    "fetching": "拉取远程提交",
    "validating": "校验 OtterWiki 文档",
    "waiting": "等待空间请求结束",
    "switching": "切换空间仓库",
    "applying": "快进更新本地分支",
    "pushing": "推送指定分支",
    "finishing": "完成后处理",
    "done": "已完成",
}
_BRANCH_RE = re.compile(r"^[^\s~^:?*\\\[\]-][^\s~^:?*\\\[]*$")
_SCP_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+:[^/].+$")


class RepositorySyncError(ValueError):
    pass


def _sync_root():
    return git_sync_root(task_root(app.config))


def _json(data, status=200):
    response = jsonify(data)
    response.status_code = status
    return response


def validate_remote_url(value):
    value = (value or "").strip()
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise RepositorySyncError("请填写有效的远程仓库地址。")
    if value.startswith("-") or value.startswith("ext::"):
        raise RepositorySyncError("不支持该 Git 仓库协议。")
    if _SCP_RE.fullmatch(value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "ssh") or not parsed.hostname:
        raise RepositorySyncError("仓库地址仅支持 HTTPS 或 SSH。")
    if parsed.password is not None:
        raise RepositorySyncError("请勿在仓库 URL 中嵌入密码或令牌。")
    return value


def validate_branch(value):
    value = (value or "").strip()
    if (
        not value
        or len(value) > 255
        or not _BRANCH_RE.fullmatch(value)
        or value.endswith((".", "/"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or value == "@"
    ):
        raise RepositorySyncError("请填写有效的 Git 分支名。")
    return value


def _is_https(url):
    return urlsplit(url).scheme == "https"


class GitCredentials:
    """Temporary, non-interactive environment for one Git operation."""

    def __init__(self, record):
        self.record = record
        self.directory = None
        self.env = {"GIT_TERMINAL_PROMPT": "0"}

    def __enter__(self):
        secret = decrypt_secret(self.record.secret_ciphertext)
        if self.record.auth_type == "https" and secret:
            self.directory = Path(
                tempfile.mkdtemp(prefix="otterwiki-git-auth-")
            )
            askpass = self.directory / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf '%s\\n' \"$OTTERWIKI_GIT_USERNAME\" ;;\n"
                "  *) printf '%s\\n' \"$OTTERWIKI_GIT_SECRET\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            self.env.update(
                GIT_ASKPASS=str(askpass),
                OTTERWIKI_GIT_USERNAME=self.record.username or "git",
                OTTERWIKI_GIT_SECRET=secret,
            )
        elif self.record.auth_type == "ssh" and secret:
            self.directory = Path(
                tempfile.mkdtemp(prefix="otterwiki-git-auth-")
            )
            key = self.directory / "identity"
            key.write_text(secret.rstrip() + "\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.env["GIT_SSH_COMMAND"] = (
                "ssh -o BatchMode=yes -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=yes -i " + shlex.quote(str(key))
            )
        return self.env

    def __exit__(self, *_args):
        if self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)


def _task_lock(space_id):
    return ProcessLock(_sync_root() / ("space-%s" % space_id))


def _head(repo):
    try:
        return repo.head.commit.hexsha
    except (ValueError, TypeError, git.GitError):
        return None


def _serialize_task(task):
    now = time.time()
    return {
        "id": task.id,
        "repository_id": task.repository_id,
        "space_id": task.space_id,
        "operation": task.operation,
        "trigger": task.trigger,
        "status": task.status,
        "phase": task.phase,
        "phase_label": PHASE_LABELS.get(task.phase, task.phase),
        "before_commit": task.before_commit,
        "after_commit": task.after_commit,
        "elapsed_seconds": max(
            0, int((task.finished_at or now) - task.created_at)
        ),
        "updated_at": task.updated_at,
        "result": task.result,
        "error": task.error,
        "status_url": url_for("admin_repository_sync_task", task_id=task.id),
    }


def _set_phase(task_id, phase):
    task = db.session.get(GitSyncTask, task_id)
    if task:
        task.phase = phase
        task.updated_at = time.time()
        db.session.commit()


def _validate_tree(repo, revision="HEAD"):
    try:
        commit = repo.commit(revision)
    except (ValueError, git.GitError) as error:
        raise RepositorySyncError("远程分支没有可用提交。") from error
    markdown = 0
    for item in commit.tree.traverse():
        path = PurePosixPath(item.path)
        if any(part in ("", ".", "..", ".git") for part in path.parts):
            raise RepositorySyncError("仓库包含不安全路径。")
        if item.type == "submodule" or item.mode == 0o160000:
            raise RepositorySyncError("仓库不能包含 Git 子模块。")
        if item.mode == 0o120000:
            raise RepositorySyncError("仓库不能包含符号链接。")
        if item.type == "blob" and item.path.lower().endswith(".md"):
            markdown += 1
            try:
                item.data_stream.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise RepositorySyncError(
                    f"Markdown 文件不是 UTF-8 编码：{item.path}"
                ) from error
    if markdown < 1:
        raise RepositorySyncError("仓库至少需要包含一个 Markdown 文档。")
    return commit.hexsha, markdown


def _journal(task, target, **extra):
    data = {
        "id": task.id,
        "kind": "git-sync",
        "space_id": task.space_id,
        "target": str(Path(target).resolve()),
        "old_commit": task.before_commit,
        "status": "running",
        "phase": task.phase,
        "maintenance": True,
        "workspace": task.workspace,
    }
    data.update(extra)
    write_journal(_sync_root(), data)
    task.checkpoint = data
    db.session.commit()
    return data


def _finish_repository_change(storage):
    from otterwiki.structured_navigation import (
        clear_structured_navigation_cache,
    )

    clear_structured_navigation_cache()
    storage.notify_repository_changed_from_external()


def _ensure_space_active(space_id):
    db.session.expire_all()
    space = db.session.get(Space, space_id)
    if not space or space.is_archived:
        raise RepositorySyncError("空间已归档或删除，未更新本地仓库。")
    return space


def _import_repository(task, record, space, storage, env):
    target = Path(storage.path).resolve()
    workspace = target.parent / (".otterwiki-git-sync-" + task.id)
    workspace.mkdir(parents=True, exist_ok=False)
    task.workspace = str(workspace)
    db.session.commit()
    stage = workspace / "repository"
    _set_phase(task.id, "cloning")
    try:
        cloned = git.Repo.clone_from(
            record.remote_url,
            stage,
            branch=record.branch,
            single_branch=True,
            env=env,
        )
        try:
            _set_phase(task.id, "validating")
            commit, pages = _validate_tree(cloned)
            if cloned.is_dirty(untracked_files=True):
                raise RepositorySyncError("克隆后的工作树不干净。")
        finally:
            cloned.close()
        space = _ensure_space_active(space.id)
        _set_phase(task.id, "waiting")
        task = db.session.get(GitSyncTask, task.id)
        task.phase = "switching"
        journal = _journal(task, target, commit=commit, stage=str(stage))
        wait_for_readers(target)
        from otterwiki.document_import import _replace_repository

        def checkpoint(**values):
            journal.update(values, phase="switching", maintenance=True)
            write_journal(_sync_root(), journal)
            saved = db.session.get(GitSyncTask, task.id)
            saved.checkpoint = dict(journal)
            db.session.commit()

        warnings = _replace_repository(storage, stage, checkpoint)
        _set_phase(task.id, "finishing")
        Drafts.query.filter_by(space_id=space.id).delete()
        _finish_repository_change(storage)
        return commit, {"pages": pages, "warnings": warnings}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _pull_repository(task, record, storage, env):
    repo = storage.repo
    if repo.is_dirty(untracked_files=True):
        raise RepositorySyncError("本地仓库存在未提交更改，已取消拉取。")
    _set_phase(task.id, "fetching")
    with repo.git.custom_environment(**env):
        repo.git.fetch(
            record.remote_url,
            "+refs/heads/%s:refs/otterwiki/remotes/%s"
            % (record.branch, record.branch),
        )
    remote_ref = "refs/otterwiki/remotes/%s" % record.branch
    _set_phase(task.id, "validating")
    remote_commit, pages = _validate_tree(repo, remote_ref)
    local_commit = _head(repo)
    if local_commit and not repo.is_ancestor(local_commit, remote_commit):
        if repo.is_ancestor(remote_commit, local_commit):
            return local_commit, {
                "pages": pages,
                "message": "本地分支已领先远程，无需拉取。",
            }
        raise RepositorySyncError("本地与远程分支已分叉，未执行合并或覆盖。")
    if local_commit == remote_commit:
        return local_commit, {"pages": pages, "message": "本地已是最新版本。"}
    _ensure_space_active(task.space_id)
    task = db.session.get(GitSyncTask, task.id)
    task.phase = "applying"
    journal = _journal(task, storage.path, commit=remote_commit)
    wait_for_readers(storage.path)
    repo.git.merge("--ff-only", remote_ref)
    journal.update(phase="finishing", commit=remote_commit)
    write_journal(_sync_root(), journal)
    _finish_repository_change(storage)
    return remote_commit, {"pages": pages, "message": "已快进到远程分支。"}


def _push_repository(task, record, storage, env):
    _ensure_space_active(task.space_id)
    repo = storage.repo
    if repo.is_dirty(untracked_files=True):
        raise RepositorySyncError("本地仓库存在未提交更改，已取消推送。")
    commit = _head(repo)
    if not commit:
        raise RepositorySyncError("本地仓库没有可推送的提交。")
    _set_phase(task.id, "pushing")
    with repo.git.custom_environment(**env):
        repo.git.push(
            record.remote_url,
            "%s:refs/heads/%s" % (commit, record.branch),
        )
    return commit, {"message": "已推送到远程分支。"}


def _safe_error(error, record, task):
    if isinstance(error, (RepositorySyncError, CredentialError)):
        message = str(error)
    elif isinstance(error, git.GitCommandError):
        detail = (error.stderr or error.stdout or str(error)).strip()
        message = "Git 操作失败：" + detail[:2000]
    else:
        message = "Git 同步失败，请查看服务日志。"
        app.logger.exception("Git sync task %s failed", task.id)
    try:
        secret = decrypt_secret(record.secret_ciphertext)
    except CredentialError:
        secret = ""
    for value in (secret, task.workspace or ""):
        if value:
            message = message.replace(value, "***")
    return message


def _run_task(task_id, lock):
    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(5):
            with app.app_context():
                try:
                    GitSyncTask.query.filter_by(id=task_id).filter(
                        GitSyncTask.status.in_(ACTIVE)
                    ).update({"updated_at": time.time()})
                    db.session.commit()
                except Exception:
                    db.session.rollback()

    beat = None
    with app.app_context():
        task = db.session.get(GitSyncTask, task_id)
        try:
            if not task:
                return
            task.status = "running"
            task.phase = "connecting"
            task.started_at = task.updated_at = time.time()
            db.session.commit()
            beat = threading.Thread(target=heartbeat, daemon=True)
            beat.start()
            record = db.session.get(SpaceGitRepository, task.repository_id)
            space = db.session.get(Space, task.space_id)
            if not record or not space or space.is_archived:
                raise RepositorySyncError("仓库配置或空间已失效。")
            storage = get_space_storage(space)
            task.before_commit = _head(storage.repo)
            db.session.commit()
            with GitCredentials(record) as env:
                if task.operation == "import":
                    commit, result = _import_repository(
                        task, record, space, storage, env
                    )
                elif task.operation == "pull":
                    commit, result = _pull_repository(
                        task, record, storage, env
                    )
                else:
                    commit, result = _push_repository(
                        task, record, storage, env
                    )
            task = db.session.get(GitSyncTask, task_id)
            record = db.session.get(SpaceGitRepository, task.repository_id)
            task.status = "succeeded"
            task.phase = "done"
            task.after_commit = commit
            task.result = result
            task.finished_at = task.updated_at = time.time()
            record.state = "ready"
            record.last_synced_commit = commit
            if task.operation == "import" and record.initialized_at is None:
                from datetime import UTC, datetime

                record.initialized_at = datetime.now(UTC)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            task = db.session.get(GitSyncTask, task_id)
            record = (
                db.session.get(SpaceGitRepository, task.repository_id)
                if task
                else None
            )
            if task:
                task.status = "failed"
                task.error = (
                    _safe_error(error, record, task) if record else str(error)
                )
                task.finished_at = task.updated_at = time.time()
                db.session.commit()
        finally:
            heartbeat_stop.set()
            if beat:
                beat.join(timeout=1)
            try:
                data = next(
                    (
                        j
                        for j in journals(_sync_root())
                        if j.get("id") == task_id
                    ),
                    None,
                )
                if data:
                    finished = db.session.get(GitSyncTask, task_id)
                    if finished and finished.status == "succeeded":
                        data.update(maintenance=False, status="succeeded")
                        write_journal(_sync_root(), data)
                        if data.get("backup"):
                            try:
                                from otterwiki.document_import import (
                                    _remove_tree_with_retries,
                                )

                                _remove_tree_with_retries(Path(data["backup"]))
                            except OSError:
                                app.logger.exception(
                                    "Unable to remove Git import backup"
                                )
                        remove_journal(_sync_root(), task_id)
                    else:
                        recovered = recover_journal(_sync_root(), data)
                        if finished:
                            if recovered.get("commit") == _head(
                                get_space_storage(
                                    db.session.get(Space, finished.space_id)
                                ).repo
                            ):
                                finished.status = "interrupted"
                            recovery_error = recovered.get("error")
                            if recovery_error:
                                finished.error = (
                                    (finished.error + " ")
                                    if finished.error
                                    else ""
                                ) + recovery_error
                            finished.checkpoint = recovered
                            db.session.commit()
                        if not recovered.get("maintenance"):
                            remove_journal(_sync_root(), task_id)
            finally:
                lock.release()
            # Commits made while this space was busy set a durable pending
            # bit.  Check it after releasing the lock and coalesce them into
            # one follow-up push.
            try:
                finished = db.session.get(GitSyncTask, task_id)
                pending_record = (
                    db.session.get(SpaceGitRepository, finished.repository_id)
                    if finished and finished.repository_id
                    else None
                )
                if (
                    pending_record
                    and pending_record.auto_push_enabled
                    and pending_record.auto_push_pending
                    and pending_record.state == "ready"
                ):
                    pending_record.auto_push_pending = False
                    db.session.commit()
                    queue_task(
                        pending_record,
                        "push",
                        trigger="auto_push",
                        request_key=uuid.uuid4().hex,
                    )
            except Exception:
                db.session.rollback()
                app.logger.exception("Unable to queue pending automatic push")


def queue_task(
    record, operation, trigger="manual", user_id=None, request_key=None
):
    try:
        import uwsgi

        if uwsgi.numproc > 1:
            raise RepositorySyncError(
                "异步 Git 同步要求单 Web 进程，请设置 processes=1。"
            )
    except ImportError:
        pass
    if operation not in OPERATIONS:
        raise RepositorySyncError("不支持的 Git 同步操作。")
    if record.state != "ready" and operation != "import":
        raise RepositorySyncError("请先完成首次导入。")
    space = db.session.get(Space, record.space_id)
    if not space or space.is_archived:
        raise RepositorySyncError("归档空间不能执行 Git 同步。")
    if blocked(task_root(app.config), space_repository_path(space)):
        raise RepositorySyncError("该空间正在执行文档导入或 Git 同步。")
    request_key = request_key or uuid.uuid4().hex
    existing = GitSyncTask.query.filter_by(
        repository_id=record.id,
        operation=operation,
        request_key=request_key,
    ).first()
    if existing:
        return existing, False
    active = (
        GitSyncTask.query.filter_by(space_id=record.space_id)
        .filter(GitSyncTask.status.in_(ACTIVE))
        .order_by(GitSyncTask.created_at.desc())
        .first()
    )
    if active:
        return active, False
    lock = _task_lock(record.space_id)
    if not lock.acquire():
        active = (
            GitSyncTask.query.filter_by(space_id=record.space_id)
            .filter(GitSyncTask.status.in_(ACTIVE))
            .first()
        )
        if active:
            return active, False
        raise RepositorySyncError("该空间已有 Git 任务正在执行。")
    try:
        now = time.time()
        task = GitSyncTask(
            id=uuid.uuid4().hex,
            repository_id=record.id,
            space_id=record.space_id,
            user_id=user_id,
            operation=operation,
            trigger=trigger,
            request_key=request_key,
            status="queued",
            phase="queued",
            created_at=now,
            updated_at=now,
        )
        db.session.add(task)
        db.session.commit()
    except Exception:
        lock.release()
        raise
    thread = threading.Thread(
        target=_run_task,
        args=(task.id, lock),
        name="git-sync-" + task.id,
        daemon=True,
    )
    thread.start()
    return task, True


def recover_tasks():
    for task in GitSyncTask.query.filter(GitSyncTask.status.in_(ACTIVE)).all():
        lock = _task_lock(task.space_id)
        if not lock.acquire():
            continue
        try:
            data = next(
                (j for j in journals(_sync_root()) if j.get("id") == task.id),
                None,
            )
            if data and data.get("maintenance"):
                recover_journal(_sync_root(), data)
            task.status = "interrupted"
            task.error = "任务已中断，未自动重跑，请核对后重试。"
            task.finished_at = task.updated_at = time.time()
            db.session.commit()
            if data and not data.get("maintenance"):
                remove_journal(_sync_root(), task.id)
        finally:
            lock.release()


def repository_management_form():
    if not has_permission("ADMIN"):
        abort(403)
    repository_schema_ready = True
    try:
        recover_tasks()
        records = SpaceGitRepository.query.order_by(
            SpaceGitRepository.id
        ).all()
        configured = {item.space_id for item in records}
        spaces = Space.query.order_by(Space.name).all()
        latest = {}
        credential_errors = {}
        for record in records:
            latest_task = (
                GitSyncTask.query.filter_by(repository_id=record.id)
                .order_by(GitSyncTask.created_at.desc())
                .first()
            )
            latest[record.id] = (
                _serialize_task(latest_task) if latest_task else None
            )
            if record.secret_ciphertext:
                try:
                    decrypt_secret(record.secret_ciphertext)
                except CredentialError as error:
                    credential_errors[record.id] = str(error)
    except OperationalError as error:
        # Keep real spaces visible in the page context.  Previously every
        # repository schema error was presented as "no available spaces",
        # which hid an incomplete migration behind a misleading empty state.
        db.session.rollback()
        repository_schema_ready = False
        records, configured, latest, credential_errors = [], set(), {}, {}
        try:
            spaces = Space.query.order_by(Space.name).all()
        except OperationalError:
            db.session.rollback()
            spaces = []
        app.logger.warning(
            "repository management schema is not ready: %s", error
        )
    webhook_url = ""
    legacy_url = str(app.config.get("GIT_REMOTE_PULL_URL") or "")
    legacy_push_url = str(app.config.get("GIT_REMOTE_PUSH_URL") or "")
    if app.config.get("GIT_REMOTE_PULL_ENABLED") and legacy_url:
        from otterwiki.util import (
            compute_webhook_hash,
            compute_webhook_hash_legacy,
        )

        legacy_hash = (
            compute_webhook_hash(app.config["SECRET_KEY"], legacy_url)
            if app.config.get("GIT_REMOTE_PULL_URL_SECURE")
            else compute_webhook_hash_legacy(legacy_url)
        )
        webhook_url = url_for(
            "pull_webhook", webhook_hash=legacy_hash, _external=True
        )
    return render_template(
        "admin/repository_management.html",
        title="仓库管理",
        repositories=records,
        repository_spaces={space.id: space for space in spaces},
        available_spaces=[
            space
            for space in spaces
            if not space.is_archived and space.id not in configured
        ],
        latest_tasks=latest,
        repository_credential_errors=credential_errors,
        repository_schema_ready=repository_schema_ready,
        webhook_url=webhook_url,
        git_action_result=None,
        legacy_remote_locked=any(
            space.is_default and space.id in configured for space in spaces
        ),
        legacy_repository_conflict=bool(
            (legacy_url and legacy_push_url and legacy_url != legacy_push_url)
            or (
                app.config.get("GIT_REMOTE_PULL_PRIVATE_KEY")
                and app.config.get("GIT_REMOTE_PUSH_PRIVATE_KEY")
                and app.config.get("GIT_REMOTE_PULL_PRIVATE_KEY")
                != app.config.get("GIT_REMOTE_PUSH_PRIVATE_KEY")
            )
        ),
    )


def save_repository(form):
    if not has_permission("ADMIN"):
        abort(403)
    try:
        record_id = form.get("repository_id")
        record = (
            db.session.get(SpaceGitRepository, int(record_id))
            if record_id
            else None
        )
        if record and (
            GitSyncTask.query.filter_by(repository_id=record.id)
            .filter(GitSyncTask.status.in_(ACTIVE))
            .first()
        ):
            raise RepositorySyncError("仓库任务正在执行，完成前不能修改配置。")
        if record is None:
            space_id = int(form.get("space_id", ""))
            space = db.session.get(Space, space_id)
            if not space or space.is_archived:
                raise RepositorySyncError("请选择有效的未归档空间。")
            if SpaceGitRepository.query.filter_by(space_id=space_id).first():
                raise RepositorySyncError("该空间已绑定远程仓库。")
            record = SpaceGitRepository(space_id=space_id)
            db.session.add(record)
        old_identity = (record.remote_url, record.branch)
        old_auth_type = record.auth_type
        record.remote_url = validate_remote_url(form.get("remote_url"))
        record.branch = validate_branch(form.get("branch") or "main")
        auth_type = form.get("auth_type", "none")
        if auth_type not in ("none", "https", "ssh"):
            raise RepositorySyncError("请选择有效的认证方式。")
        if auth_type == "https" and not _is_https(record.remote_url):
            raise RepositorySyncError("HTTPS 认证只能用于 HTTPS 仓库地址。")
        if auth_type == "ssh" and _is_https(record.remote_url):
            raise RepositorySyncError("SSH 认证不能用于 HTTPS 仓库地址。")
        record.auth_type = auth_type
        record.username = (
            (form.get("username") or "").strip() or None
            if auth_type == "https"
            else None
        )
        secret = (
            (
                form.get("https_secret")
                if auth_type == "https"
                else form.get("ssh_secret")
            )
            or form.get("secret")
            or ""
        )
        if (
            form.get("clear_secret") == "true"
            or auth_type == "none"
            or (old_auth_type not in (None, auth_type) and not secret)
        ):
            record.secret_ciphertext = None
        elif secret:
            record.secret_ciphertext = encrypt_secret(secret)
        record.auto_push_enabled = form.get("auto_push_enabled") == "true"
        if old_identity != (None, None) and old_identity != (
            record.remote_url,
            record.branch,
        ):
            record.state = "uninitialized"
            record.initialized_at = None
            record.last_synced_commit = None
        db.session.commit()
        toast("空间 Git 仓库配置已保存。")
    except (
        RepositorySyncError,
        CredentialError,
        IntegrityError,
        TypeError,
        ValueError,
    ) as error:
        db.session.rollback()
        message = (
            "该空间已绑定远程仓库。"
            if isinstance(error, IntegrityError)
            else str(error)
        )
        toast(message, "error")
    return repository_management_form()


def delete_repository(record_id):
    if not has_permission("ADMIN"):
        abort(403)
    record = db.session.get(SpaceGitRepository, record_id)
    if not record:
        abort(404)
    if (
        GitSyncTask.query.filter_by(repository_id=record.id)
        .filter(GitSyncTask.status.in_(ACTIVE))
        .first()
    ):
        return _json({"error": "存在运行中的仓库任务，不能删除配置。"}, 409)
    GitSyncTask.query.filter_by(repository_id=record.id).update(
        {"repository_id": None}
    )
    db.session.delete(record)
    db.session.commit()
    toast("仓库配置已删除，空间文档和本地 Git 历史保持不变。")
    return repository_management_form()


def submit_admin_task(record_id, form):
    if not has_permission("ADMIN"):
        abort(403)
    record = db.session.get(SpaceGitRepository, record_id)
    if not record:
        abort(404)
    operation = form.get("operation", "")
    if operation == "import":
        space = db.session.get(Space, record.space_id)
        expected = "IMPORT " + space.slug
        if form.get("confirmation", "").strip() != expected:
            return _json(
                {"error": f"请输入 {expected} 以确认替换空间仓库。"}, 400
            )
    key = form.get("request_key") or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", key):
        return _json({"error": "同步请求标识无效。"}, 400)
    try:
        task, _created = queue_task(
            record, operation, user_id=current_user.id, request_key=key
        )
        return _json(_serialize_task(task), 202)
    except RepositorySyncError as error:
        return _json({"error": str(error)}, 409)


def get_admin_task(task_id):
    if not has_permission("ADMIN"):
        abort(403)
    task = db.session.get(GitSyncTask, task_id)
    if not task:
        abort(404)
    return _json(_serialize_task(task))


def regenerate_webhook(record_id):
    if not has_permission("ADMIN"):
        abort(403)
    record = db.session.get(SpaceGitRepository, record_id)
    if not record:
        abort(404)
    token = secrets.token_urlsafe(32)
    record.webhook_token_hash = hashlib.sha256(token.encode()).hexdigest()
    record.legacy_webhook_hash = None
    db.session.commit()
    return _json(
        {
            "webhook_url": url_for(
                "repository_pull_webhook",
                repository_id=record.id,
                token=token,
                _external=True,
            )
        }
    )


def webhook_pull(record_id, token):
    record = db.session.get(SpaceGitRepository, record_id)
    supplied = hashlib.sha256(token.encode()).hexdigest()
    if (
        not record
        or not record.webhook_token_hash
        or not hmac.compare_digest(record.webhook_token_hash, supplied)
    ):
        abort(404)
    try:
        task, _created = queue_task(record, "pull", trigger="webhook")
        return _json({"id": task.id, "status": task.status}, 202)
    except RepositorySyncError as error:
        return _json({"error": str(error)}, 409)


def legacy_webhook_pull(webhook_hash):
    """Compatibility path for the default-space webhook migrated by v5."""
    try:
        record = SpaceGitRepository.query.filter_by(
            legacy_webhook_hash=webhook_hash
        ).first()
    except OperationalError:
        abort(404)
    if not record:
        abort(404)
    try:
        task, _created = queue_task(record, "pull", trigger="webhook")
        return _json({"id": task.id, "status": task.status}, 202)
    except RepositorySyncError as error:
        return _json({"error": str(error)}, 409)


def schedule_auto_push(storage_path):
    """Queue a push for the record matching a concrete storage path."""
    try:
        for record in SpaceGitRepository.query.filter_by(
            auto_push_enabled=True, state="ready"
        ).all():
            space = db.session.get(Space, record.space_id)
            if (
                space
                and Path(space_repository_path(space)).resolve()
                == Path(storage_path).resolve()
            ):
                active = (
                    GitSyncTask.query.filter_by(space_id=record.space_id)
                    .filter(GitSyncTask.status.in_(ACTIVE))
                    .first()
                )
                if active:
                    record.auto_push_pending = True
                    db.session.commit()
                    return True
                try:
                    queue_task(record, "push", trigger="auto_push")
                except RepositorySyncError:
                    record.auto_push_pending = True
                    db.session.commit()
                return True
    except (OperationalError, RepositorySyncError, RuntimeError):
        return False
    return False
