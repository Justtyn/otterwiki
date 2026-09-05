#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""持久化任务、维护隔离与中断恢复；只使用临时数据。"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import git
import pytest

from otterwiki import import_tasks
from otterwiki.import_runtime import (
    ProcessLock,
    blocked,
    enter_repository,
    recover_journal,
    recover_before_storage,
    repo_commit,
    task_root,
    wait_for_readers,
    write_journal,
)
from test_document_import import _write_apstack_source, _zip_source
from test_space_regressions import _run_isolated

URL = "/-/admin/document_import"


def payload(source, key="async-import-request-123456"):
    return dict(
        confirmation="RESET APSTACK",
        request_key=key,
        source_directory=str(source),
    )


@pytest.fixture
def held_task(monkeypatch):
    pending = []
    monkeypatch.setattr(
        import_tasks, "start_task", lambda *a: pending.append(a)
    )
    yield pending
    for args in pending:
        args[-2].release()


def test_admission_idempotency_permissions_and_restore(
    admin_client, other_client, app_with_user, tmp_path, held_task
):
    from otterwiki.models import Space
    from otterwiki.server import db

    source = _write_apstack_source(tmp_path / "source")
    app_with_user.config["WTF_CSRF_ENABLED"] = False
    app_with_user.config["GIT_WEB_SERVER"] = True
    response = admin_client.post(URL, data=payload(source))
    assert response.status_code == 202
    task = response.json
    assert (
        admin_client.post(URL, data=payload(source)).json["id"] == task["id"]
    )
    assert (
        admin_client.post(
            URL, data=payload(source, "second-request-123456")
        ).status_code
        == 409
    )
    # 刷新会找回任务，执行锁仍持有时不能误报中断。
    assert task["id"] in admin_client.get(URL).text
    status = admin_client.get(task["status_url"])
    assert status.headers["Cache-Control"] == "no-store"
    assert status.json["status"] == "queued"
    assert "workspace" not in status.json and "checkpoint" not in status.json
    assert str(tmp_path) not in status.text
    assert admin_client.get("/Home").status_code == 423
    assert other_client.get("/Home").status_code == 423
    assert other_client.get(task["status_url"]).status_code == 403
    assert other_client.post(URL, data=payload(source)).status_code == 403
    assert (
        admin_client.get("/.git/info/refs?service=git-upload-pack").status_code
        == 423
    )
    assert admin_client.post("/-/api/v1/pull/invalid").status_code == 423
    assert admin_client.get("/-/housekeeping").status_code == 423
    space = Space(
        slug="other", name="其他空间", is_archived=False, is_default=False
    )
    db.session.add(space)
    db.session.commit()
    from otterwiki.spaces import get_space_storage

    get_space_storage(space).store(
        filename="home.md",
        content="# 其他空间",
        author=("test", "test@example.org"),
        message="test",
    )
    assert admin_client.get("/-/s/other/").status_code in (200, 302)
    assert (
        admin_client.get("/-/s/other" + task["status_url"]).status_code == 404
    )
    import_tasks.run_task(*held_task.pop())
    finished = admin_client.get(task["status_url"]).json
    assert finished["status"] == "succeeded", finished
    assert finished["result"]["commit"] == repo_commit(
        app_with_user.storage.path
    )
    assert not finished["maintenance"]
    assert admin_client.get("/apstack6").status_code == 200


def test_thread_start_failure_releases_everything(
    admin_client, app_with_user, tmp_path, monkeypatch
):
    from otterwiki.models import DocumentImportTask

    def fail(*args):
        raise RuntimeError("无法启动线程")

    monkeypatch.setattr(import_tasks, "start_task", fail)
    response = admin_client.post(
        URL, data=payload(_write_apstack_source(tmp_path / "source"))
    )
    assert response.status_code == 500
    task = DocumentImportTask.query.one()
    assert task.status == "failed"
    assert not Path(task.workspace).exists()
    root = task_root(app_with_user.config)
    assert not blocked(root, app_with_user.storage.path)
    lock = ProcessLock(root)
    assert lock.acquire()
    lock.release()


def test_released_executor_lock_marks_active_task_interrupted(
    admin_client, app_with_user, tmp_path, held_task
):
    old = repo_commit(app_with_user.storage.path)
    response = admin_client.post(
        URL, data=payload(_write_apstack_source(tmp_path / "source"))
    )
    args = held_task.pop()
    args[-2].release()  # 模拟 Web 进程退出后由操作系统释放执行锁。

    import_tasks.recover_tasks()
    state = admin_client.get(response.json["status_url"]).json

    assert state["status"] == "interrupted"
    assert not state["maintenance"]
    assert repo_commit(app_with_user.storage.path) == old


@pytest.mark.parametrize(
    "failure", ["invalid", "traversal", "limit", "conversion"]
)
def test_async_failures_keep_original_repo(
    failure, admin_client, app_with_user, tmp_path, held_task, monkeypatch
):
    from otterwiki.document_import import DocumentImportError

    old = repo_commit(app_with_user.storage.path)
    archive = io.BytesIO()
    if failure == "invalid":
        archive.write(b"not a zip")
    elif failure == "conversion":
        archive = _zip_source(_write_apstack_source(tmp_path / "source"))
    else:
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(
                "../escape" if failure == "traversal" else "docs/index.md",
                "# test",
            )
    archive.seek(0)
    if failure == "limit":
        app_with_user.config["APSTACK_IMPORT_MAX_EXTRACTED_SIZE"] = 1
    response = admin_client.post(
        URL,
        data={
            "confirmation": "RESET APSTACK",
            "request_key": "bad-archive-request-123456",
            "archive": (archive, "source.zip"),
        },
    )
    assert response.status_code == 202
    if failure == "conversion":
        import otterwiki.document_import as document_import

        monkeypatch.setattr(
            document_import,
            "_run_migration",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                DocumentImportError("模拟转换失败")
            ),
        )
    import_tasks.run_task(*held_task.pop())
    status = admin_client.get(response.json["status_url"]).json
    assert status["status"] == "failed", status
    assert not status["maintenance"]
    assert repo_commit(app_with_user.storage.path) == old


def test_archived_space_and_csrf_reject_submission(
    admin_client, app_with_user, tmp_path
):
    from flask.testing import FlaskClient
    from otterwiki.server import db
    from otterwiki.spaces import get_default_space

    assert (
        FlaskClient.post(admin_client, URL, data=payload(tmp_path)).status_code
        == 400
    )
    get_default_space().is_archived = True
    db.session.commit()
    assert admin_client.post(URL, data=payload(tmp_path)).status_code == 409


def test_real_worker_returns_immediately_and_waits_for_existing_requests(
    tmp_path,
):
    _write_apstack_source(tmp_path / "source")
    _run_isolated(
        tmp_path,
        '''
import threading, time
from otterwiki.migrations import run_migrations
from otterwiki.import_runtime import enter_repository, task_root
from otterwiki import import_tasks, document_import
from otterwiki.models import DocumentImportTask
with app.app_context():
    run_migrations()
    user = SimpleAuth.User(name='admin', email='admin@example.org', is_admin=True,
        is_approved=True, email_confirmed=True, first_seen=datetime.now(UTC), last_seen=datetime.now(UTC))
    db.session.add(user); db.session.commit(); uid = user.id
client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(uid); session['_fresh'] = True
release = enter_repository(task_root(app.config), repo)
entered = threading.Event(); proceed = threading.Event()
original = document_import._run_migration
threads = []
start = import_tasks.start_task
def capture(*args):
    t = threading.Thread(target=import_tasks.run_task, args=args)
    threads.append(t); t.start()
import_tasks.start_task = capture
def slow(*args, **kwargs):
    entered.set()
    assert proceed.wait(10)
    return original(*args, **kwargs)
document_import._run_migration = slow
try:
    before = time.monotonic()
    r = client.post('/-/admin/document_import', data={
        'confirmation':'RESET APSTACK', 'request_key':'real-thread-request-123456',
        'source_directory': str(root / 'source')})
    assert r.status_code == 202, r.text
    assert time.monotonic() - before < 2
    url = r.json['status_url']
    assert not entered.wait(.1)  # 已进入请求还没退出，不能开始重建
    assert client.get(url).json['status'] in ('queued', 'running')
    release()
    assert entered.wait(5)
    # 后台卡在耗时转换时仍能查询，并且旧心跳不能覆盖执行锁。
    with app.app_context():
        task = db.session.get(DocumentImportTask, r.json['id'])
        task.updated_at = 1; db.session.commit()
    assert client.get(url).json['status'] == 'running'
    assert client.get('/Home').status_code == 423
finally:
    release(); proceed.set()
    for t in threads: t.join(15)
assert not any(t.is_alive() for t in threads)
result = client.get(url).json
assert result['status'] == 'succeeded', result
assert result['result']['pages'] == 3
''',
    )


def make_repo(path, text):
    repo = git.Repo.init(path)
    (path / "home.md").write_text(text)
    repo.git.add(all=True)
    commit = repo.index.commit(
        text, author=git.Actor("test", "test@example.org")
    ).hexsha
    repo.close()
    return commit


@pytest.mark.parametrize("point", ["before", "between", "after", "unknown"])
def test_checkpoint_recovery(point, tmp_path):
    target, backup, stage = (
        tmp_path / name for name in ("repo", "backup", "stage")
    )
    old = make_repo(target, "old")
    new = make_repo(stage, "new")
    data = dict(
        id="checkpoint-test",
        target=str(target),
        backup=str(backup),
        stage=str(stage),
        old_commit=old,
        commit=new,
        phase="switching",
        status="running",
        maintenance=True,
    )
    if point == "before":
        data["phase"] = "converting"
    else:
        os.replace(target, backup)
        if point == "after":
            os.replace(stage, target)
        if point == "unknown":
            target.mkdir()
            (target / "unknown.txt").write_text("unknown")
    root = tmp_path / "tasks"
    write_journal(root, data)
    recover_before_storage(
        {"REPOSITORY": str(target), "DOCUMENT_IMPORT_TASK_ROOT": str(root)}
    )
    result = json.loads((root / "checkpoint-test.json").read_text())
    assert result["status"] == "interrupted"
    assert result["maintenance"] == (point == "unknown")
    if point in ("before", "between"):
        assert repo_commit(target) == old
    elif point == "after":
        assert repo_commit(target) == new and backup.exists()
        assert "仓库已切换" in result["error"]
    else:
        assert backup.exists() and (target / "unknown.txt").exists()
    # 再执行不会重复导入或抹除现场。
    recover_before_storage(
        {"REPOSITORY": str(target), "DOCUMENT_IMPORT_TASK_ROOT": str(root)}
    )
    assert blocked(root, target) == (point == "unknown")


def test_cross_process_lock_and_request_timeout(tmp_path):
    root = tmp_path / "tasks"
    lock = ProcessLock(root)
    assert lock.acquire()
    code = "from pathlib import Path; from otterwiki.import_runtime import ProcessLock; import sys; l=ProcessLock(Path(sys.argv[1])); print(l.acquire())"
    r = subprocess.run(
        [sys.executable, "-c", code, str(root)], capture_output=True, text=True
    )
    assert r.returncode == 0 and r.stdout.strip() == "False"
    lock.release()
    r = subprocess.run(
        [sys.executable, "-c", code, str(root)], capture_output=True, text=True
    )
    assert r.stdout.strip() == "True"
    release = enter_repository(root, tmp_path / "repo")
    try:
        with pytest.raises(TimeoutError):
            wait_for_readers(tmp_path / "repo", timeout=0.01)
    finally:
        release()
    wait_for_readers(tmp_path / "repo", timeout=0.01)


def test_corrupt_checkpoint_fails_closed(tmp_path):
    target = tmp_path / "repo"
    make_repo(target, "old")
    root = tmp_path / "tasks"
    root.mkdir()
    (root / "broken.json").write_text("{half", encoding="utf-8")

    recover_before_storage(
        {"REPOSITORY": str(target), "DOCUMENT_IMPORT_TASK_ROOT": str(root)}
    )

    assert blocked(root, target)
    assert repo_commit(target)


def test_pre_switch_missing_target_stays_in_maintenance(tmp_path):
    target = tmp_path / "missing"
    root = tmp_path / "tasks"
    data = {
        "id": "missing-target",
        "target": str(target),
        "old_commit": "f" * 40,
        "phase": "converting",
        "status": "running",
        "maintenance": True,
    }
    write_journal(root, data)

    recover_before_storage(
        {"REPOSITORY": str(target), "DOCUMENT_IMPORT_TASK_ROOT": str(root)}
    )

    assert blocked(root, target)
    assert not target.exists()


def test_v4_upgrade_rollback_and_repeat(tmp_path):
    _run_isolated(
        tmp_path,
        '''
from sqlalchemy import inspect
from otterwiki.migrations import MIGRATIONS, run_migrations
original = MIGRATIONS.pop(4)
run_migrations()
with app.app_context():
    assert 'document_import_task' not in inspect(db.engine).get_table_names()
def fail():
    original()
    raise RuntimeError('模拟 v4 写入后失败')
MIGRATIONS[4] = fail
try:
    run_migrations()
except RuntimeError:
    pass
else:
    raise AssertionError('应当回滚')
with app.app_context():
    assert 'document_import_task' not in inspect(db.engine).get_table_names()
    assert {v.version for v in SchemaVersion.query.all()} == {1,2,3}
MIGRATIONS[4] = original
run_migrations(); run_migrations()
with app.app_context():
    assert 'document_import_task' in inspect(db.engine).get_table_names()
    assert {v.version for v in SchemaVersion.query.all()} == {1,2,3,4}
''',
        legacy=True,
    )


def test_extraction_and_conversion_report_real_counts(tmp_path):
    from otterwiki.document_import import (
        extract_zip_safely,
        ImportLimits,
        _run_migration,
    )

    source = _write_apstack_source(tmp_path / "source")
    zip_path = tmp_path / "source.zip"
    zip_path.write_bytes(_zip_source(source).read())
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    events = []
    extract_zip_safely(
        zip_path, extracted, ImportLimits(), lambda *a: events.append(a)
    )
    with zipfile.ZipFile(zip_path) as z:
        last = events[-1]
        assert last[1] == last[2] == len(z.infolist())
        assert last[3] == sum(i.file_size for i in z.infolist())
    events = []
    _run_migration(
        source,
        tmp_path / "stage",
        ImportLimits(),
        ("test", "test@example.org"),
        lambda *a: events.append(a),
    )
    for phase in ("converting", "verifying"):
        values = [e for e in events if e[0] == phase]
        assert values[0][1] == 0
        assert values[-1][1] == values[-1][2] > 0
        assert [e[1] for e in values] == list(range(values[-1][2] + 1))


def test_failure_after_switch_retains_backup_and_reports_interruption(
    admin_client, app_with_user, tmp_path, held_task, monkeypatch
):
    from otterwiki.models import DocumentImportTask

    original = import_tasks.Progress.__call__

    def fail_after_switch(self, phase, *args, **kwargs):
        if phase == 'finishing':
            raise RuntimeError('模拟已替换但结果尚未提交时中断')
        return original(self, phase, *args, **kwargs)

    monkeypatch.setattr(import_tasks.Progress, '__call__', fail_after_switch)
    old = repo_commit(app_with_user.storage.path)
    response = admin_client.post(
        URL, data=payload(_write_apstack_source(tmp_path / 'source'))
    )
    import_tasks.run_task(*held_task.pop())
    status = admin_client.get(response.json['status_url']).json
    assert status['status'] == 'interrupted'
    assert '仓库已切换' in status['error']
    assert not status['maintenance']
    assert repo_commit(app_with_user.storage.path) != old
    task = DocumentImportTask.query.one()
    assert repo_commit(task.checkpoint['backup']) == old
    assert Path(task.workspace).exists()
    # 再次查询和刷新不重跑。
    assert (
        admin_client.get(response.json['status_url']).json['status']
        == 'interrupted'
    )
    assert response.json['id'] in admin_client.get(URL).text


def test_draft_cleanup_failure_is_success_with_warning(
    admin_client, app_with_user, tmp_path, held_task, monkeypatch
):
    from otterwiki.models import Drafts

    query_type = type(Drafts.query)
    original = query_type.delete

    def fail_drafts(self, *args, **kwargs):
        if self.column_descriptions[0]['entity'] is Drafts:
            raise RuntimeError('模拟草稿清理失败')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(query_type, 'delete', fail_drafts)
    response = admin_client.post(
        URL, data=payload(_write_apstack_source(tmp_path / 'source'))
    )
    import_tasks.run_task(*held_task.pop())
    state = admin_client.get(response.json['status_url']).json
    assert state['status'] == 'succeeded'
    assert any('草稿' in w for w in state['result']['warnings'])
    assert not state['maintenance']


def test_revoked_space_before_worker_does_not_replace(
    admin_client, app_with_user, tmp_path, held_task
):
    from otterwiki.spaces import get_default_space
    from otterwiki.server import db

    old = repo_commit(app_with_user.storage.path)
    response = admin_client.post(
        URL, data=payload(_write_apstack_source(tmp_path / 'source'))
    )
    get_default_space().is_archived = True
    db.session.commit()
    import_tasks.run_task(*held_task.pop())
    state = admin_client.get(response.json['status_url']).json
    assert state['status'] == 'failed' and '授权已失效' in state['error']
    assert repo_commit(app_with_user.storage.path) == old
