#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

import re
import pytest
import os
import otterwiki.gitstorage
from datetime import datetime
from flask.testing import FlaskClient

# Snapshot of app.config, captured the first time create_app runs (before any
# test has mutated it). app.config is a module-level singleton just like storage
# and db, so config a test sets leaks into the following tests unless it is
# restored. See create_app below.
_config_snapshot = None

# 兜底：部分纯渲染测试不请求 create_app，但 otterwiki.renderer 的惰性
# 导入（otterwiki.spaces -> models -> server）仍需要完整有效的
# OTTERWIKI_SETTINGS（SECRET_KEY + REPOSITORY）。在收集后、首个测试前
# 提供一个带已初始化仓库的最小配置；create_app 会按需覆盖为各自的临时配置。
if not os.environ.get("OTTERWIKI_SETTINGS"):
    import tempfile

    _fallback_dir = tempfile.mkdtemp(prefix="otterwiki-fallback-")
    _fallback_repo = os.path.join(_fallback_dir, "repo")
    os.mkdir(_fallback_repo)
    otterwiki.gitstorage.GitStorage(path=_fallback_repo, initialize=True)
    _fallback_cfg = os.path.join(_fallback_dir, "settings.cfg")
    with open(_fallback_cfg, "w") as _f:
        _f.writelines(
            [
                "REPOSITORY = '{}'\n".format(_fallback_repo),
                "SECRET_KEY = 'Testing Testing Testing'\n",
                "MAIL_SUPPRESS_SEND = True\n",
            ]
        )
    os.environ["OTTERWIKI_SETTINGS"] = _fallback_cfg
    # 以生产启动顺序预先完成 server 引导，避免测试触发 renderer 的惰性
    # 导入时形成 spaces -> models -> server 的循环引用
    import otterwiki.server  # noqa: F401


class CSRFTestClient(FlaskClient):
    """Test client that automatically injects CSRF tokens."""

    def _get_csrf_token(self):
        response = super().get('/', follow_redirects=True)
        html = response.data.decode()
        match = re.search(
            r'name="csrf-token" content="([^"]+)"',
            html,
        )
        if match is None:
            # 登录/注册等页面没有 csrf-token meta，只有隐藏表单域
            match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        return match.group(1) if match else None

    def post(self, *args, **kwargs):
        data = kwargs.get('data')
        if data is None:
            token = self._get_csrf_token()
            if token:
                kwargs['data'] = {'csrf_token': token}
        elif isinstance(data, dict) and 'csrf_token' not in data:
            token = self._get_csrf_token()
            if token:
                data['csrf_token'] = token
        return super().post(*args, **kwargs)


@pytest.fixture
def create_app(tmpdir):
    tmpdir.mkdir("repo")
    _storage = otterwiki.gitstorage.GitStorage(
        path=str(tmpdir.join("repo")), initialize=True
    )
    settings_cfg = str(tmpdir.join("settings.cfg"))
    # write config file
    with open(settings_cfg, "w") as f:
        f.writelines(
            [
                "REPOSITORY = '{}'\n".format(str(_storage.path)),
                "SITE_NAME = 'TEST WIKI'\n",
                "DEBUG = True\n",  # enable test and debug settings
                "TESTING = True\n",
                "MAIL_SUPPRESS_SEND = True\n",
                "SECRET_KEY = 'Testing Testing Testing'\n",
            ]
        )
    # configure environment
    os.environ["OTTERWIKI_SETTINGS"] = settings_cfg
    # get app
    from otterwiki.server import app, db, mail, storage

    # app.config is a module-level singleton, so any config a test sets leaks
    # into the following tests. Capture a baseline snapshot the first time and
    # restore it for every test so each starts from the same configuration.
    global _config_snapshot
    if _config_snapshot is None:
        _config_snapshot = dict(app.config)
    else:
        app.config.clear()
        app.config.update(_config_snapshot)
    # keep REPOSITORY pointing at this test's repository
    app.config["REPOSITORY"] = _storage.path

    # otterwiki.server runs its setup once at import time, so app, db and
    # storage are module-level singletons shared by every test. Without a
    # reset the first test's repository and database leak into all the
    # following ones (untracked files, users, preferences, ...). Re-point the
    # shared objects at this test's fresh repository and database so each test
    # runs in isolation. All modules hold a reference to this same storage
    # object, so mutating it in place reaches every one of them.
    storage.path = _storage.path  # pyright: ignore
    storage.repo = _storage.repo  # pyright: ignore
    # give each test a clean database
    with app.app_context():
        db.drop_all()
        db.create_all()
        # 多空间：新库需要重建默认空间行，并清空空间仓库缓存
        from otterwiki.spaces import ensure_default_space, reset_space_caches

        reset_space_caches()
        ensure_default_space()
    # a fresh repository is empty; recreate the initial home page just like a
    # real instance does on first start (see otterwiki/server.py)
    if (
        len(storage.list()[0]) < 1 and len(storage.log()) < 1
    ):  # pyright: ignore
        with open(os.path.join(app.root_path, "initial_home.md")) as f:
            storage.store(  # pyright: ignore
                filename="home.md",
                content=f.read(),
                author=("Otterwiki Robot", "noreply@otterwiki"),
                message="Initial commit",
            )

    # for debugging
    app._otterwiki_tempdir = storage.path  # pyright: ignore
    # for other tests
    app.storage = storage  # pyright: ignore
    # store mail in app for testing
    app.test_mail = mail  # pyright: ignore
    # enable test and debug settings
    app.config["TESTING"] = True
    app.config["DEBUG"] = True
    app.test_client_class = CSRFTestClient

    # flask-login 0.6.3 把当前用户缓存在 g 上（g._login_user 绑定 app 上下文），
    # 而测试用例通过 req_ctx 在整个用例期间共享同一个 app 上下文（内存数据库
    # 依赖它存活）。若不清除，第一个登录的用户会“泄漏”到后续所有 client 的
    # 请求里（匿名 client 也变成已登录）。每个请求开始时强制清除缓存，
    # 由 flask-login 按 client 自己的会话 Cookie 重新加载用户。
    from flask import g

    def _force_request_scope_reset():  # pyright: ignore
        # flask-login 的当前用户缓存
        g.pop("_login_user", None)
        # flask-wtf 的 CSRF token 缓存（否则登录页会复用上一个 client
        # 的 token，而其原始值不在本 client 的会话里，导致 CSRF 校验失败）
        g.pop("csrf_token", None)
        g.pop("csrf_valid", None)

    _force_request_scope_reset._is_test_user_reload_hook = (
        True  # pyright: ignore
    )
    # 移除上一个用例遗留的同类钩子，避免钩子列表无限增长
    app.before_request_funcs[None] = [
        f
        for f in app.before_request_funcs[None]
        if not getattr(f, "_is_test_user_reload_hook", False)
    ]
    app.before_request_funcs[None].insert(0, _force_request_scope_reset)
    yield app
    # make sure GIT_REMOTE_*_ENABLED is false to avoid weird side effects
    app.config["GIT_REMOTE_PULL_ENABLED"] = False
    app.config["GIT_REMOTE_PUSH_ENABLED"] = False


def _seed_default_users(app):
    """预置标准管理员并加入默认阅读组，对应迁移 v1 之后的实例形态。"""
    from otterwiki.auth import SimpleAuth, generate_password_hash
    from otterwiki.models import Group, GroupSpaceAuth, UserGroup
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    with app.app_context():
        default_space = ensure_default_space()
        group = Group.query.filter_by(name="默认阅读组").first()
        if group is None:
            group = Group(name="默认阅读组")
            db.session.add(group)
            db.session.commit()
        if (
            GroupSpaceAuth.query.filter_by(
                group_id=group.id, space_id=default_space.id
            ).first()
            is None
        ):
            db.session.add(
                GroupSpaceAuth(group_id=group.id, space_id=default_space.id)
            )
            db.session.commit()
        admin = SimpleAuth.User.query.filter_by(
            email="mail@example.org"
        ).first()
        if admin is None:
            admin = SimpleAuth.User(
                name="Test User",
                email="mail@example.org",
                password_hash=generate_password_hash(
                    "password1234", method="scrypt"
                ),
                first_seen=datetime.now(),
                last_seen=datetime.now(),
                is_admin=True,
            )
            db.session.add(admin)
            db.session.commit()
        if (
            UserGroup.query.filter_by(
                user_id=admin.id, group_id=group.id
            ).first()
            is None
        ):
            db.session.add(UserGroup(user_id=admin.id, group_id=group.id))
            db.session.commit()


@pytest.fixture
def test_client(create_app):
    """已登录管理员的客户端：多空间升级后页面阅读需要登录与组成员资格，
    绝大多数内容测试在此形态下运行。匿名行为用 anon_client 断言。"""
    _seed_default_users(create_app)
    client = create_app.test_client()
    result = client.post(
        "/-/login",
        data={"email": "mail@example.org", "password": "password1234"},
        follow_redirects=True,
    )
    assert "登录成功。" in result.data.decode()
    return client


@pytest.fixture
def anon_client(create_app):
    """匿名（未登录）客户端：用于断言登录重定向等门禁行为。"""
    return create_app.test_client()


@pytest.fixture
def req_ctx(create_app):
    with create_app.test_request_context() as ctx:
        yield ctx


@pytest.fixture
def app_with_user(create_app, req_ctx):
    # req_ctx 让整个用例共享同一个 app 上下文：内存数据库中的数据
    # （用户、组、空间）才能被测试期间的所有请求访问。
    # 共享上下文带来的 flask-login 用户缓存泄漏由 create_app 中注册的
    # _force_user_reload 钩子在每请求开始时清除。
    from otterwiki.auth import SimpleAuth, generate_password_hash, db
    from otterwiki.models import Group, GroupSpaceAuth, UserGroup
    from otterwiki.spaces import ensure_default_space

    def _default_read_group():
        """对应迁移 v1：默认空间 + 默认阅读组，成员后续按用户添加。"""
        default_space = ensure_default_space()
        group = Group.query.filter_by(name="默认阅读组").first()
        if group is None:
            group = Group(name="默认阅读组")
            db.session.add(group)
            db.session.commit()
        if (
            GroupSpaceAuth.query.filter_by(
                group_id=group.id, space_id=default_space.id
            ).first()
            is None
        ):
            db.session.add(
                GroupSpaceAuth(group_id=group.id, space_id=default_space.id)
            )
            db.session.commit()
        return group, default_space

    # delete all users
    db.session.query(SimpleAuth.User).delete()
    db.session.commit()
    # create a user
    user = SimpleAuth.User(  # pyright: ignore
        name="Test User",
        email="mail@example.org",
        password_hash=generate_password_hash("password1234", method="scrypt"),
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        is_admin=True,
    )
    db.session.add(user)

    # create a non admin user
    user = SimpleAuth.User(  # pyright: ignore
        name="Another User",
        email="another@user.org",
        password_hash=generate_password_hash("password4567", method="scrypt"),
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        is_admin=False,
        is_approved=True,
        email_confirmed=True,
    )
    db.session.add(user)
    db.session.commit()

    # 两个用户都加入默认阅读组（多空间升级后默认空间的阅读授权）
    group, default_space = _default_read_group()
    for u in SimpleAuth.User.query.all():
        db.session.add(UserGroup(user_id=u.id, group_id=group.id))
    db.session.commit()

    yield create_app


@pytest.fixture(scope="function")
def admin_client(app_with_user):
    client = app_with_user.test_client()
    result = client.post(
        "/-/login",
        data={
            "email": "mail@example.org",
            "password": "password1234",
        },
        follow_redirects=True,
    )
    html = result.data.decode()
    assert "登录成功。" in html
    return client


@pytest.fixture(scope="function")
def other_client(app_with_user):
    client = app_with_user.test_client()
    result = client.post(
        "/-/login",
        data={
            "email": "another@user.org",
            "password": "password4567",
        },
        follow_redirects=True,
    )
    html = result.data.decode()
    assert "登录成功。" in html
    return client


#
# 多空间测试辅助
#


@pytest.fixture(scope="function")
def group_factory(app_with_user):
    """创建用户组；可选成员与可阅读空间。"""

    def _factory(name, members=(), spaces=()):
        from otterwiki.models import Group, GroupSpaceAuth, UserGroup
        from otterwiki.auth import SimpleAuth
        from otterwiki.server import db

        group = Group(name=name)
        db.session.add(group)
        db.session.commit()
        for email in members:
            user = SimpleAuth.User.query.filter_by(email=email).first()
            if user is None:
                raise ValueError(f"unknown user {email!r}")
            db.session.add(UserGroup(user_id=user.id, group_id=group.id))
        for space in spaces:
            db.session.add(
                GroupSpaceAuth(group_id=group.id, space_id=space.id)
            )
        db.session.commit()
        return group

    return _factory


@pytest.fixture(scope="function")
def space_factory(app_with_user):
    """创建新空间（独立 git 仓库 + 初始首页）。"""

    def _factory(slug, name=None, authorized_groups=()):
        from otterwiki.models import GroupSpaceAuth
        from otterwiki.server import db
        from otterwiki.spaces import get_space_storage

        space = _create_space_row(slug, name or slug)
        storage = get_space_storage(space)
        if len(storage.log()) < 1:
            storage.store(
                filename="home.md",
                content=f"# {name or slug}\n\n空间首页。\n",
                author=("OtterWiki Test", "test@example.org"),
                message="初始化空间",
            )
        for group in authorized_groups:
            db.session.add(
                GroupSpaceAuth(group_id=group.id, space_id=space.id)
            )
        db.session.commit()
        return space

    def _create_space_row(slug, name):
        from datetime import UTC, datetime

        from otterwiki.models import Space, db

        space = Space(
            slug=slug,
            name=name,
            is_archived=False,
            is_default=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.session.add(space)
        db.session.commit()
        return space

    return _factory
