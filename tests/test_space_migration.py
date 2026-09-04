#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""版本化数据库迁移测试：升级、重复执行与失败恢复语义。"""

import pytest


@pytest.fixture
def migration_env(app_with_user):
    """提供迁移相关对象（延迟导入，避免收集期初始化 app）。"""
    from otterwiki.migrations import DEFAULT_GROUP_NAME, run_migrations
    from otterwiki.models import (
        Drafts,
        Group,
        GroupSpaceAuth,
        SchemaVersion,
        Space,
        User,
        UserGroup,
    )
    from otterwiki.server import app, db

    return {
        "DEFAULT_GROUP_NAME": DEFAULT_GROUP_NAME,
        "run_migrations": run_migrations,
        "Drafts": Drafts,
        "Group": Group,
        "GroupSpaceAuth": GroupSpaceAuth,
        "SchemaVersion": SchemaVersion,
        "Space": Space,
        "User": User,
        "UserGroup": UserGroup,
        "app": app,
        "db": db,
    }


def _legacy_db(env):
    """清空用户并构造升级前的旧库状态：不同权限的本地用户 + 历史草稿。"""
    from datetime import datetime

    from otterwiki.auth import SimpleAuth, generate_password_hash

    db = env["db"]

    db.drop_all()
    db.create_all()

    users = {
        "admin": SimpleAuth.User(
            name="Admin",
            email="admin@example.org",
            password_hash=generate_password_hash("password1234"),
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            is_admin=True,
        ),
        "reader": SimpleAuth.User(
            name="Reader",
            email="reader@example.org",
            password_hash=generate_password_hash("password1234"),
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            is_approved=True,
            allow_read=True,
        ),
        "plain": SimpleAuth.User(
            name="Plain",
            email="plain@example.org",
            password_hash=generate_password_hash("password1234"),
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            is_approved=True,
        ),
    }
    for user in users.values():
        db.session.add(user)
    db.session.commit()

    # 历史草稿（无空间归属）
    db.session.add(
        env["Drafts"](
            pagepath="Home",
            revision="",
            author_email="reader@example.org",
            content="旧草稿内容",
            datetime=datetime.now(),
            space_id=None,
        )
    )
    db.session.commit()
    return users


def _default_group(env):
    return env["Group"].query.filter_by(name=env["DEFAULT_GROUP_NAME"]).first()


def _member_ids(env, group):
    return {
        row[0]
        for row in env["db"]
        .session.query(env["UserGroup"].user_id)
        .filter_by(group_id=group.id)
        .all()
    }


def test_migration_v1_seeds_default_space_and_group(migration_env):
    env = migration_env
    users = _legacy_db(env)

    env["run_migrations"]()

    default_space = env["Space"].query.filter_by(is_default=True).first()
    assert default_space is not None
    assert default_space.slug == "default"

    group = _default_group(env)
    assert group is not None

    # 授权默认空间
    assert (
        env["GroupSpaceAuth"]
        .query.filter_by(group_id=group.id, space_id=default_space.id)
        .first()
        is not None
    )

    # READ_ACCESS 默认 ANONYMOUS：所有本地用户都具备阅读资格并进入默认组
    member_ids = _member_ids(env, group)
    assert users["admin"].id in member_ids
    assert users["reader"].id in member_ids
    assert users["plain"].id in member_ids

    # 历史草稿归入默认空间
    draft = env["Drafts"].query.filter_by(pagepath="Home").first()
    assert draft.space_id == default_space.id

    # 版本已记录
    assert 1 in {v.version for v in env["SchemaVersion"].query.all()}


def test_migration_v1_read_access_registered(migration_env):
    env = migration_env
    users = _legacy_db(env)
    env["app"].config["READ_ACCESS"] = "REGISTERED"
    try:
        env["run_migrations"]()
        group = _default_group(env)
        member_ids = _member_ids(env, group)
        assert users["plain"].id in member_ids
    finally:
        env["app"].config["READ_ACCESS"] = "ANONYMOUS"


def test_migration_is_repeatable(migration_env):
    env = migration_env
    _legacy_db(env)

    env["run_migrations"]()
    env["run_migrations"]()

    group = _default_group(env)
    assert group is not None
    assert (
        env["Group"].query.filter_by(name=env["DEFAULT_GROUP_NAME"]).count()
        == 1
    )
    assert env["Space"].query.filter_by(is_default=True).count() == 1


def test_removed_members_not_reseeded(migration_env):
    """重复执行不得重新添加已被管理员移除的组成员。"""
    env = migration_env
    _legacy_db(env)
    env["run_migrations"]()

    group = _default_group(env)
    removed = env["User"].query.filter_by(email="plain@example.org").first()
    row = (
        env["UserGroup"]
        .query.filter_by(user_id=removed.id, group_id=group.id)
        .first()
    )
    env["db"].session.delete(row)
    env["db"].session.commit()

    env["run_migrations"]()

    assert (
        env["UserGroup"]
        .query.filter_by(user_id=removed.id, group_id=group.id)
        .first()
        is None
    )
