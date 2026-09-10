from pathlib import Path
import time
from types import SimpleNamespace

import git
import pytest
from sqlalchemy import inspect, text

from otterwiki.credentials import decrypt_secret
from otterwiki.models import GitSyncTask, SpaceGitRepository
from otterwiki.repository_sync import (
    RepositorySyncError,
    queue_task,
    validate_branch,
    validate_remote_url,
)


def _wait(db, task_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db.session.expire_all()
        task = db.session.get(GitSyncTask, task_id)
        if task.status not in ("queued", "running"):
            return task
        time.sleep(0.05)
    raise AssertionError("Git sync task did not finish")


def _remote_repository(tmp_path, filename="Home.md"):
    source_path = tmp_path / "source"
    bare_path = tmp_path / "remote.git"
    source = git.Repo.init(source_path)
    (source_path / filename).write_text("# Remote Home\n", encoding="utf-8")
    source.index.add([filename])
    source.index.commit(
        "remote initial",
        author=git.Actor("Test", "test@example.invalid"),
    )
    bare = git.Repo.init(bare_path, bare=True)
    source.create_remote("origin", str(bare_path))
    source.git.push("--set-upstream", "origin", "HEAD:refs/heads/main")
    return source, bare


def test_remote_and_branch_validation():
    assert validate_remote_url("https://example.com/team/wiki.git")
    assert validate_remote_url("http://example.com/team/wiki.git")
    assert validate_remote_url("git@example.com:team/wiki.git")
    assert validate_remote_url("ssh://git@example.com/team/wiki.git")
    assert validate_remote_url("https://gitlab.example.com/team/wiki.git")
    # 协议黑名单必须大小写不敏感；内网/回环/链路本地地址一律拒绝
    rejected = (
        "file:///tmp/repo",
        "ext::touch /tmp/x",
        "EXT::touch /tmp/x",
        "File::/tmp/repo",
        "-upload-pack=x",
        "C:\\repo",
        "git@example.com:../repo.git",
        "http://127.0.0.1:8080/repo.git",
        "http://169.254.169.254/latest/meta-data",
        "http://10.1.2.3/repo.git",
        "http://192.168.1.9/repo.git",
        "http://172.16.3.4/repo.git",
        "http://localhost:3000/repo.git",
        "ssh://root@localhost/repo.git",
        "https://[::1]/repo.git",
        "git@10.1.2.3:team/repo.git",
        "http://user:password@example.com/repo.git",
        "ftp://example.com/repo.git",
    )
    for value in rejected:
        with pytest.raises(RepositorySyncError):
            validate_remote_url(value)
    for value in ("-main", "bad..branch", "refs/@{bad", "bad branch"):
        with pytest.raises(RepositorySyncError):
            validate_branch(value)


def test_repository_form_encrypts_secret_and_enforces_one_space(
    app_with_user, admin_client
):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    space = ensure_default_space()
    response = admin_client.post(
        "/-/admin/repository_management/repositories",
        data={
            "space_id": str(space.id),
            "remote_url": "https://example.com/team/wiki.git",
            "branch": "main",
            "auth_type": "https",
            "username": "robot",
            "secret": "top-secret-token",
            "auto_push_enabled": "true",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "top-secret-token" not in html
    record = SpaceGitRepository.query.filter_by(space_id=space.id).one()
    assert record.secret_ciphertext != "top-secret-token"
    assert decrypt_secret(record.secret_ciphertext) == "top-secret-token"
    assert record.auto_push_enabled is True

    duplicate = admin_client.post(
        "/-/admin/repository_management/repositories",
        data={
            "space_id": str(space.id),
            "remote_url": "https://example.com/other.git",
            "branch": "main",
            "auth_type": "none",
        },
    )
    assert duplicate.status_code == 200
    assert "已绑定远程仓库" in duplicate.data.decode()
    assert SpaceGitRepository.query.count() == 1


def test_repository_form_accepts_plain_http_with_warning(
    app_with_user, admin_client
):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    space = ensure_default_space()
    response = admin_client.post(
        "/-/admin/repository_management/repositories",
        data={
            "space_id": str(space.id),
            "remote_url": "http://code.example.com/team/wiki.git",
            "branch": "main",
            "auth_type": "https",
            "username": "robot",
            "secret": "plaintext-pat",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "明文传输" in html
    record = SpaceGitRepository.query.filter_by(space_id=space.id).one()
    assert record.remote_url == "http://code.example.com/team/wiki.git"
    assert decrypt_secret(record.secret_ciphertext) == "plaintext-pat"

    # Editing the bound record to SSH auth over an HTTP URL must be rejected.
    ssh_mismatch = admin_client.post(
        "/-/admin/repository_management/repositories",
        data={
            "repository_id": str(record.id),
            "remote_url": "http://code.example.com/team/wiki.git",
            "branch": "main",
            "auth_type": "ssh",
        },
    )
    assert ssh_mismatch.status_code == 200
    assert "SSH 认证不能用于 HTTP(S)" in ssh_mismatch.data.decode()
    assert SpaceGitRepository.query.count() == 1


def test_import_pull_push_and_divergence(app_with_user, tmp_path):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space, get_space_storage

    source, bare = _remote_repository(tmp_path)
    space = ensure_default_space()
    storage = get_space_storage(space)
    old_commit = storage.repo.head.commit.hexsha
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url=str(Path(bare.working_dir)),
        branch="main",
        auth_type="none",
        state="uninitialized",
    )
    db.session.add(record)
    db.session.commit()

    task, created = queue_task(
        record, "import", user_id=1, request_key="a" * 16
    )
    assert created is True
    task = _wait(db, task.id)
    assert task.status == "succeeded", task.error
    assert task.before_commit == old_commit
    storage = get_space_storage(space)
    assert (Path(storage.path) / "Home.md").read_text() == "# Remote Home\n"
    assert storage.repo.head.commit.hexsha == bare.commit("main").hexsha

    (Path(source.working_dir) / "Guide.md").write_text(
        "# Guide\n", encoding="utf-8"
    )
    source.index.add(["Guide.md"])
    source.index.commit(
        "remote guide", author=git.Actor("Test", "test@example.invalid")
    )
    source.git.push("origin", "HEAD:refs/heads/main")
    db.session.refresh(record)
    pull, _ = queue_task(record, "pull", request_key="b" * 16)
    pull = _wait(db, pull.id)
    assert pull.status == "succeeded", pull.error
    assert (Path(storage.path) / "Guide.md").exists()

    storage.store(
        "Local.md",
        "# Local\n",
        message="local page",
        author=("Test", "test@example.invalid"),
    )
    local_commit = storage.repo.head.commit.hexsha
    push, _ = queue_task(record, "push", request_key="c" * 16)
    push = _wait(db, push.id)
    assert push.status == "succeeded", push.error
    bare.close()
    bare = git.Repo(str(tmp_path / "remote.git"))
    assert bare.commit("main").hexsha == local_commit

    # Make independent local and remote commits from the same common base.
    storage.store(
        "Local2.md",
        "# Local 2\n",
        message="local diverges",
        author=("Test", "test@example.invalid"),
    )
    before = storage.repo.head.commit.hexsha
    source.git.fetch("origin", "main")
    source.git.reset("--hard", "FETCH_HEAD")
    (Path(source.working_dir) / "Remote2.md").write_text(
        "# Remote 2\n", encoding="utf-8"
    )
    source.index.add(["Remote2.md"])
    source.index.commit(
        "remote diverges", author=git.Actor("Test", "test@example.invalid")
    )
    source.git.push("origin", "HEAD:refs/heads/main")
    db.session.refresh(record)
    conflict, _ = queue_task(record, "pull", request_key="d" * 16)
    conflict = _wait(db, conflict.id)
    assert conflict.status == "failed"
    assert "分叉" in conflict.error
    assert storage.repo.head.commit.hexsha == before


def test_import_refreshes_request_repository_metadata(
    app_with_user, admin_client, tmp_path
):
    from otterwiki.import_runtime import blocked, task_root
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space, space_repository_path

    source, bare = _remote_repository(tmp_path, filename="home.md")
    space = ensure_default_space()
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url=str(Path(bare.working_dir)),
        branch="main",
        auth_type="none",
        state="uninitialized",
    )
    db.session.add(record)
    db.session.commit()

    task, _created = queue_task(
        record, "import", user_id=1, request_key="metadata-refresh"
    )
    task = _wait(db, task.id)

    assert task.status == "succeeded", task.error
    deadline = time.time() + 2
    while blocked(
        task_root(app_with_user.config), space_repository_path(space)
    ):
        assert time.time() < deadline
        time.sleep(0.01)
    page = admin_client.get("/home")
    assert page.status_code == 200
    assert "未纳入版本控制".encode() not in page.data
    source.close()
    bare.close()


def test_task_api_permissions_and_webhook(
    app_with_user, admin_client, other_client, anon_client, monkeypatch
):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    space = ensure_default_space()
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url="https://example.com/wiki.git",
        branch="main",
        auth_type="none",
        state="ready",
    )
    db.session.add(record)
    db.session.commit()
    response = admin_client.post(
        f"/-/admin/repository_management/repositories/{record.id}/webhook"
    )
    assert response.status_code == 200
    webhook_url = response.get_json()["webhook_url"]
    assert "example.com/wiki.git" not in webhook_url
    assert record.webhook_token_hash not in webhook_url

    monkeypatch.setattr(
        "otterwiki.repository_sync.queue_task",
        lambda *_args, **_kwargs: (
            SimpleNamespace(id="webhook-task", status="queued"),
            True,
        ),
    )
    path = webhook_url.split("http://localhost", 1)[-1]
    webhook_response = anon_client.post(path)
    assert webhook_response.status_code == 202
    assert webhook_response.get_json()["id"] == "webhook-task"
    assert anon_client.post(path + "invalid").status_code == 404

    response = other_client.get("/-/admin/repository_management")
    assert response.status_code == 403
    response = other_client.post(
        f"/-/admin/repository_management/repositories/{record.id}/tasks",
        data={"operation": "pull", "request_key": "x" * 16},
    )
    assert response.status_code == 403


def test_auto_push_marks_pending_when_space_is_busy(app_with_user):
    from otterwiki.repository_sync import schedule_auto_push
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space, space_repository_path

    space = ensure_default_space()
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url="https://example.com/wiki.git",
        branch="main",
        auth_type="none",
        state="ready",
        auto_push_enabled=True,
    )
    db.session.add(record)
    db.session.flush()
    now = time.time()
    db.session.add(
        GitSyncTask(
            id="busy" * 8,
            repository_id=record.id,
            space_id=space.id,
            operation="pull",
            trigger="manual",
            request_key="busy" * 4,
            status="running",
            phase="fetching",
            created_at=now,
            updated_at=now,
        )
    )
    db.session.commit()
    assert schedule_auto_push(space_repository_path(space)) is True
    db.session.refresh(record)
    assert record.auto_push_pending is True


def test_v5_migrates_legacy_ssh_secret_and_clears_plaintext(app_with_user):
    from otterwiki.migrations import MIGRATIONS
    from otterwiki.models import Preferences
    from otterwiki.server import app, db

    remote = "git@example.com:team/wiki.git"
    private_key = "legacy-private-key"
    values = {
        "GIT_REMOTE_PUSH_ENABLED": "True",
        "GIT_REMOTE_PULL_ENABLED": "True",
        "GIT_REMOTE_PUSH_URL": remote,
        "GIT_REMOTE_PULL_URL": remote,
        "GIT_REMOTE_PUSH_PRIVATE_KEY": private_key,
        "GIT_REMOTE_PULL_PRIVATE_KEY": private_key,
    }
    for name, value in values.items():
        db.session.add(Preferences(name=name, value=value))
        app.config[name] = (
            value.lower() == "true" if "ENABLED" in name else value
        )
    db.session.commit()

    MIGRATIONS[5]()
    db.session.commit()

    record = SpaceGitRepository.query.one()
    assert record.auth_type == "ssh"
    assert record.remote_url == remote
    assert decrypt_secret(record.secret_ciphertext) == private_key
    assert record.legacy_webhook_hash
    for name in (
        "GIT_REMOTE_PUSH_PRIVATE_KEY",
        "GIT_REMOTE_PULL_PRIVATE_KEY",
        "GIT_REMOTE_PUSH_URL",
        "GIT_REMOTE_PULL_URL",
    ):
        assert db.session.get(Preferences, name).value == ""


def test_v6_repairs_repository_table_created_by_early_v5(app_with_user):
    from otterwiki.migrations import MIGRATIONS
    from otterwiki.server import db

    GitSyncTask.__table__.drop(bind=db.engine)
    SpaceGitRepository.__table__.drop(bind=db.engine)
    db.session.execute(
        text(
            """
            CREATE TABLE space_git_repository (
                id INTEGER NOT NULL PRIMARY KEY,
                space_id INTEGER NOT NULL UNIQUE,
                remote_url TEXT NOT NULL,
                branch VARCHAR(255) NOT NULL,
                auth_type VARCHAR(16) NOT NULL,
                username VARCHAR(512),
                secret_ciphertext TEXT,
                auto_push_enabled BOOLEAN NOT NULL,
                auto_push_pending BOOLEAN NOT NULL,
                webhook_token_hash VARCHAR(64),
                state VARCHAR(24) NOT NULL,
                last_synced_commit VARCHAR(64),
                initialized_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
    )
    db.session.commit()

    MIGRATIONS[6]()
    db.session.commit()

    columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("space_git_repository")
    }
    assert "legacy_webhook_hash" in columns


def test_repository_page_reports_incomplete_schema(
    app_with_user, admin_client
):
    from otterwiki.server import db

    GitSyncTask.__table__.drop(bind=db.engine)
    SpaceGitRepository.__table__.drop(bind=db.engine)

    response = admin_client.get("/-/admin/repository_management")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Git 仓库管理的数据表尚未完成升级" in html
    assert "暂无可配置的未归档空间" not in html


def test_repository_page_does_not_poll_completed_task(
    app_with_user, admin_client
):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    space = ensure_default_space()
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url="https://example.com/wiki.git",
        branch="main",
        auth_type="none",
        state="ready",
    )
    db.session.add(record)
    db.session.flush()
    now = time.time()
    task = GitSyncTask(
        id="completed-import-task",
        repository_id=record.id,
        space_id=space.id,
        operation="import",
        trigger="manual",
        request_key="completed-task-key",
        status="succeeded",
        phase="done",
        created_at=now - 2,
        updated_at=now,
        started_at=now - 2,
        finished_at=now,
    )
    db.session.add(task)
    db.session.commit()

    html = admin_client.get("/-/admin/repository_management").data.decode()

    assert "import · 已完成 · succeeded" in html
    assert 'data-status-url=' not in html


def test_repository_page_polls_active_task(
    app_with_user, admin_client, monkeypatch
):
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    space = ensure_default_space()
    record = SpaceGitRepository(
        space_id=space.id,
        remote_url="https://example.com/wiki.git",
        branch="main",
        auth_type="none",
        state="ready",
    )
    db.session.add(record)
    db.session.flush()
    now = time.time()
    task = GitSyncTask(
        id="running-pull-task",
        repository_id=record.id,
        space_id=space.id,
        operation="pull",
        trigger="manual",
        request_key="running-task-key",
        status="running",
        phase="fetching",
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    db.session.add(task)
    db.session.commit()
    monkeypatch.setattr(
        "otterwiki.repository_sync.recover_tasks", lambda: None
    )

    html = admin_client.get("/-/admin/repository_management").data.decode()

    assert 'data-status-url=' in html
    assert "running-pull-task" in html
