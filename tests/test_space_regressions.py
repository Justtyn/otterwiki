#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""多空间审查问题的回归测试；独立进程中的数据库均位于 pytest 临时目录。"""

import os
import subprocess
import sys
import textwrap
from datetime import datetime

import pytest


@pytest.mark.parametrize("via_cli", [False, True])
def test_deleted_user_id_cannot_inherit_groups(
    admin_client, app_with_user, via_cli
):
    from otterwiki.auth import SimpleAuth, generate_password_hash
    from otterwiki.models import UserGroup
    from otterwiki.server import db

    old = SimpleAuth.User.query.filter_by(email="another@user.org").one()
    old_id = old.id
    assert UserGroup.query.filter_by(user_id=old_id).count()
    if via_cli:
        result = app_with_user.test_cli_runner().invoke(
            args=["user", "delete", old.email, "--confirm"]
        )
        assert result.exit_code == 0, result.output
    else:
        result = admin_client.post(
            f"/-/user/{old_id}", data={"delete": "true"}
        )
        assert result.status_code == 302
    assert not UserGroup.query.filter_by(user_id=old_id).count()
    new = SimpleAuth.User(
        name="新账号",
        email="new@example.org",
        password_hash=generate_password_hash("password1234"),
        is_admin=False,
        is_approved=True,
        email_confirmed=True,
        first_seen=datetime.now(),
        last_seen=datetime.now(),
    )
    db.session.add(new)
    db.session.commit()
    assert new.id == old_id  # 实际覆盖 SQLite 复用最后一个 ID 的场景
    client = app_with_user.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(new.id)
        session["_fresh"] = True
    assert client.get("/Home").status_code == 404


def test_archived_default_space_blocks_git_and_web(
    admin_client, other_client, app_with_user
):
    from otterwiki.server import db
    from otterwiki.spaces import get_default_space, space_read_allowed
    from otterwiki.auth import SimpleAuth

    app_with_user.config["GIT_WEB_SERVER"] = True
    default = get_default_space()
    default.is_archived = True
    db.session.commit()
    reader = SimpleAuth.User.query.filter_by(email="another@user.org").one()
    assert not space_read_allowed(default, reader)
    assert not space_read_allowed(None, reader)
    assert other_client.get("/Home").status_code == 404
    refs = "/.git/info/refs?service=git-upload-pack"
    assert other_client.get(refs).status_code == 401
    # 没有浏览器会话的 Git 客户端使用 Basic Auth 也不能绕过归档。
    client = app_with_user.test_client()
    assert (
        client.get(refs, auth=("another@user.org", "password4567")).status_code
        == 403
    )
    assert admin_client.get("/Home").status_code == 200
    assert admin_client.get(refs).status_code == 200


def test_default_space_home_precedence_and_clear(admin_client, app_with_user):
    from otterwiki.server import db, storage
    from otterwiki.spaces import get_default_space

    for filename, marker in (
        ("saved.md", "保存首页"),
        ("legacy.md", "原首页"),
    ):
        storage.store(
            filename=filename,
            content=f"# {marker}\n",
            author=("测试", "test@example.org"),
            message="首页测试",
        )
    default = get_default_space()
    app_with_user.config["HOME_PAGE"] = "Legacy"
    response = admin_client.post(
        f"/-/admin/spaces/{default.id}",
        data={"name": default.name, "home_page": "Saved"},
    )
    assert response.status_code == 302
    assert "保存首页" in admin_client.get("/").text
    admin_client.post(
        f"/-/admin/spaces/{default.id}",
        data={"name": default.name, "home_page": ""},
    )
    assert "原首页" in admin_client.get("/").text
    app_with_user.config["HOME_PAGE"] = ""
    assert admin_client.get("/").status_code == 200
    app_with_user.config["HOME_PAGE"] = "/-/index"
    assert admin_client.get("/").headers["Location"] == "/-/index"
    db.session.expire_all()


def _run_isolated(tmp_path, code, legacy=False):
    """使用独立文件库，避免内存库测试夹具掩盖事务和并发连接的问题。"""
    bootstrap = f"""
import os, sqlite3
from pathlib import Path
from datetime import datetime, UTC
root = Path({str(tmp_path)!r})
repo = root / 'repo'
repo.mkdir()
from otterwiki.gitstorage import GitStorage
GitStorage(str(repo), initialize=True)
if {legacy!r}:
    with sqlite3.connect(root / 'test.sqlite') as conn:
        conn.execute('''CREATE TABLE drafts (
            id INTEGER PRIMARY KEY, pagepath VARCHAR(2048),
            revision VARCHAR(64), author_email VARCHAR(256), content TEXT,
            cursor_line INTEGER, cursor_ch INTEGER, datetime DATETIME)''')
        conn.execute("INSERT INTO drafts (pagepath, content, datetime) VALUES ('Home', 'old-draft', '2026-09-01 00:00:00')")
cfg = root / 'settings.cfg'
cfg.write_text(
    'REPOSITORY=' + repr(str(repo)) + '\\n'
    + 'SQLALCHEMY_DATABASE_URI=' + repr('sqlite:///' + str(root / 'test.sqlite')) + '\\n'
    + "SECRET_KEY='Temporary-review-secret-123456789'\\n"
    + 'WTF_CSRF_ENABLED=False\\nTESTING=True\\n'
)
os.environ['OTTERWIKI_SETTINGS'] = str(cfg)
from otterwiki.server import app, db, app_renderer
from otterwiki.models import Space, Group, UserGroup, GroupSpaceAuth, SchemaVersion, Drafts
from otterwiki.auth import SimpleAuth
from otterwiki.spaces import get_default_space, get_space_storage
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap + textwrap.dedent(code)],
        env={**os.environ, "OTTERWIKI_SETTINGS": ""},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_legacy_sqlite_migration_rolls_back_ddl_and_data(tmp_path):
    _run_isolated(
        tmp_path,
        """
from sqlalchemy import inspect
from otterwiki.migrations import MIGRATIONS, run_migrations
with app.app_context():
    assert 'space_id' not in {c['name'] for c in inspect(db.engine).get_columns('drafts')}
    original = MIGRATIONS[1]
    def fail_after_writes():
        original()
        raise RuntimeError('模拟版本记录提交前失败')
    MIGRATIONS[1] = fail_after_writes
    try:
        run_migrations()
    except RuntimeError:
        pass
    else:
        raise AssertionError('应当失败')
    assert 'space_id' not in {c['name'] for c in inspect(db.engine).get_columns('drafts')}
    assert Group.query.count() == 0
    assert GroupSpaceAuth.query.count() == 0
    assert SchemaVersion.query.count() == 0
    MIGRATIONS[1] = original
    run_migrations()
    run_migrations()
    db.session.expire_all()
    assert {v.version for v in SchemaVersion.query.all()} == {1, 2, 3, 4, 5, 6}
    assert Drafts.query.one().content == 'old-draft'
    assert Drafts.query.one().space_id == get_default_space().id
    assert Group.query.count() == 1
""",
        legacy=True,
    )


def test_v3_removes_orphans_without_restoring_revoked_grants(tmp_path):
    _run_isolated(
        tmp_path,
        """
from otterwiki.migrations import MIGRATIONS, run_migrations
with app.app_context():
    run_migrations()
    group = Group.query.one()
    user = SimpleAuth.User(name='existing', email='existing@example.org',
                          is_approved=True, first_seen=datetime.now(UTC),
                          last_seen=datetime.now(UTC))
    db.session.add(user)
    db.session.flush()
    db.session.add(UserGroup(user_id=user.id, group_id=group.id))
    GroupSpaceAuth.query.delete()  # 已撤销的授权不能被恢复
    db.session.add_all([
        UserGroup(user_id=9999, group_id=group.id),
        UserGroup(user_id=user.id, group_id=9999),
        GroupSpaceAuth(group_id=9999, space_id=get_default_space().id),
        GroupSpaceAuth(group_id=group.id, space_id=9999),
    ])
    SchemaVersion.query.filter_by(version=3).delete()
    db.session.commit()
    original = MIGRATIONS[3]
    def fail_after_cleanup():
        original()
        raise RuntimeError('模拟 v3 清理后失败')
    MIGRATIONS[3] = fail_after_cleanup
    try:
        run_migrations()
    except RuntimeError:
        pass
    else:
        raise AssertionError('应当失败')
    db.session.expire_all()
    assert UserGroup.query.count() == 3
    assert GroupSpaceAuth.query.count() == 2
    assert {v.version for v in SchemaVersion.query.all()} == {1, 2, 4, 5, 6}
    MIGRATIONS[3] = original
    run_migrations()
    run_migrations()
    db.session.expire_all()
    assert [(r.user_id, r.group_id) for r in UserGroup.query.all()] == [(user.id, group.id)]
    assert GroupSpaceAuth.query.count() == 0
    assert {v.version for v in SchemaVersion.query.all()} == {1, 2, 3, 4, 5, 6}
""",
    )


def test_concurrent_space_responses_and_embeddings_are_isolated(tmp_path):
    _run_isolated(
        tmp_path,
        """
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import g
from otterwiki.renderer import OtterwikiMdParser
with app.app_context():
    users = []
    for label in ('public', 'secret'):
        user = SimpleAuth.User(name=label, email=label+'@example.org',
            is_admin=False, is_approved=True, email_confirmed=True,
            first_seen=datetime.now(UTC), last_seen=datetime.now(UTC))
        space = Space(slug=label, name=label, is_default=False, is_archived=False)
        group = Group(name=label)
        db.session.add_all([user, space, group]); db.session.flush()
        db.session.add_all([UserGroup(user_id=user.id, group_id=group.id),
                           GroupSpaceAuth(space_id=space.id, group_id=group.id)])
        db.session.commit(); users.append(user.id)
        store = get_space_storage(space)
        store.store(filename=label+'.md', content='# '+label+'-title\\n\\n{{AttachmentList}}\\n',
                    author=('test','test@example.org'), message='test')
        store.store(filename=label+'/'+label+'.txt', content=label,
                    author=('test','test@example.org'), message='attachment')
def client(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['_user_id'] = str(uid); s['_fresh'] = True
    return c
clients = [client(uid) for uid in users]
assert clients[0].get('/-/s/secret/secret').status_code == 404
assert clients[1].get('/-/s/public/public').status_code == 404
barrier = threading.Barrier(2)
original = OtterwikiMdParser.__call__
def interleave(self, text):
    # 两个 page_render_context 均已执行，强制目录及插件渲染交错。
    barrier.wait(timeout=10)
    result = original(self, text)
    barrier.wait(timeout=10)
    return result
OtterwikiMdParser.__call__ = interleave
def fetch(index, label):
    response = clients[index].get('/-/s/'+label+'/'+label)
    assert response.status_code == 200
    assert label+'-title' in response.text
    assert '/-/s/'+label+'/'+label+'/'+label+'.txt' in response.text
    other = 'secret' if label == 'public' else 'public'
    assert other+'-title' not in response.text
    assert other+'.txt' not in response.text
with ThreadPoolExecutor(max_workers=2) as pool:
    a = pool.submit(fetch, 0, 'public')
    b = pool.submit(fetch, 1, 'secret')
    a.result(timeout=20); b.result(timeout=20)
OtterwikiMdParser.__call__ = original
""",
    )


def test_failed_render_clears_plugin_state(create_app, monkeypatch):
    from otterwiki.renderer import OtterwikiMdParser, render
    from otterwiki.render_context import render_context, render_state

    def fail(*args, **kwargs):
        raise RuntimeError("模拟解析失败")

    with render_context():
        render_state()["private-page"] = "私密页面"
        monkeypatch.setattr(OtterwikiMdParser, "__call__", fail)
        with pytest.raises(RuntimeError):
            render.markdown("# 文档", page_url="/private")
        assert not render_state()
