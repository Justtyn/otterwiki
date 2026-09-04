#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""多空间权限、隔离与 URL 前缀测试。"""

import pytest


def _create_group_with_space_access(group_factory, space, members=()):
    return group_factory(f"grp-{space.slug}", members=members, spaces=[space])


def test_slug_validation(create_app):
    # 依赖 create_app 初始化应用配置后再导入 spaces 模块
    from otterwiki.spaces import is_valid_space_slug

    assert is_valid_space_slug("docs")
    assert is_valid_space_slug("product-docs")
    assert is_valid_space_slug("a1")
    assert not is_valid_space_slug("Docs")  # 仅允许小写
    assert not is_valid_space_slug("-docs")
    assert not is_valid_space_slug("docs_2")
    assert not is_valid_space_slug("")
    assert not is_valid_space_slug("default")  # 保留标识
    assert not is_valid_space_slug("a" * 65)


def test_default_space_exists(app_with_user):
    from otterwiki.spaces import get_default_space

    space = get_default_space()
    assert space is not None
    assert space.is_default
    assert space.slug == "default"


def test_space_read_allowed_union_of_groups(
    admin_client, app_with_user, group_factory
):
    """多组授权取并集：任一所属组获准即可读。"""
    from otterwiki.auth import SimpleAuth
    from otterwiki.models import Group, GroupSpaceAuth, UserGroup
    from otterwiki.server import db
    from otterwiki.spaces import (
        get_default_space,
        space_read_allowed,
    )

    space = get_default_space()
    user = SimpleAuth.User.query.filter_by(email="another@user.org").first()

    group_a = Group(name="组A")
    group_b = Group(name="组B")
    db.session.add_all([group_a, group_b])
    db.session.commit()
    db.session.add(UserGroup(user_id=user.id, group_id=group_a.id))
    db.session.commit()

    # 只有组A授权 -> 可读
    db.session.add(GroupSpaceAuth(group_id=group_a.id, space_id=space.id))
    db.session.commit()
    assert space_read_allowed(space, user)

    # 组B 未授权，但并集已满足
    db.session.add(UserGroup(user_id=user.id, group_id=group_b.id))
    db.session.commit()
    assert space_read_allowed(space, user)

    # 全部撤销 -> 不可读
    UserGroup.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    assert not space_read_allowed(space, user)


def test_anonymous_redirects_to_login(anon_client):
    """未登录的网页访问进入登录流程。"""
    response = anon_client.get("/Home")
    assert response.status_code == 302
    assert "/-/login" in response.headers["Location"]


def test_no_group_user_gets_404(other_client):
    """无授权用户访问默认空间统一 404。"""
    from otterwiki.server import db
    from otterwiki.models import UserGroup, db
    from otterwiki.auth import SimpleAuth

    # another@user.org 默认在默认阅读组；移除其所有组成员关系
    user = SimpleAuth.User.query.filter_by(email="another@user.org").first()
    UserGroup.query.filter_by(user_id=user.id).delete()
    db.session.commit()

    response = other_client.get("/Home")
    assert response.status_code == 404


def test_admin_accesses_everything(admin_client):
    """管理员可访问所有空间。"""
    response = admin_client.get("/Home")
    assert response.status_code == 200


def test_unknown_space_404_and_login_redirect(anon_client, other_client):
    response = anon_client.get("/-/s/nope/Home")
    assert response.status_code == 302  # 匿名 -> 登录
    assert "/-/login" in response.headers["Location"]
    response = other_client.get("/-/s/nope/Home")
    assert response.status_code == 404  # 已登录 -> 404


def test_space_page_view_and_links(admin_client, space_factory, group_factory):
    """空间内页面可读，页面内链接与 WikiLink 携带空间前缀。"""
    group = group_factory("docs组")
    space = space_factory("docs", name="文档空间", authorized_groups=[group])

    # 在空间中创建一个带 WikiLink 和附件链接的页面
    from otterwiki.spaces import get_space_storage

    storage = get_space_storage(space)
    storage.store(
        filename="QuickStart.md",
        content=(
            "# 快速开始\n\n"
            "参见 [[Home]] 与 [首页](/Home)。\n\n"
            "![附件](/QuickStart/a/pic.png)\n"
        ),
        author=("tester", "t@example.org"),
        message="添加页面",
    )

    response = admin_client.get(f"/-/s/docs/QuickStart")
    assert response.status_code == 200
    html = response.data.decode()
    # WikiLink 与 markdown 链接都被补上空间前缀
    assert 'href="/-/s/docs/Home"' in html
    assert 'src="/-/s/docs/QuickStart/a/pic.png"' in html
    # 默认空间的链接不受影响
    assert 'href="/Home"' not in html


def test_two_spaces_same_page_isolated(
    admin_client, space_factory, group_factory
):
    """两个空间存在同名文档时内容互不干扰。"""
    group = group_factory("隔离组")
    space_a = space_factory("alpha", name="空间甲", authorized_groups=[group])
    space_b = space_factory("beta", name="空间乙", authorized_groups=[group])

    from otterwiki.spaces import get_space_storage

    for space, marker in ((space_a, "甲空间内容"), (space_b, "乙空间内容")):
        storage = get_space_storage(space)
        storage.store(
            filename="同名文档.md",
            content=f"# 同名文档\n\n{marker}\n",
            author=("tester", "t@example.org"),
            message="添加同名文档",
        )

    html_a = admin_client.get("/-/s/alpha/同名文档").data.decode()
    html_b = admin_client.get("/-/s/beta/同名文档").data.decode()
    assert "甲空间内容" in html_a
    assert "乙空间内容" in html_b
    assert "乙空间内容" not in html_a
    assert "甲空间内容" not in html_b


def test_revocation_takes_effect_next_request(
    other_client, app_with_user, group_factory, space_factory
):
    """撤销组成员后，从下一次请求开始生效。"""
    from otterwiki.models import UserGroup
    from otterwiki.auth import SimpleAuth
    from otterwiki.server import db

    user = SimpleAuth.User.query.filter_by(email="another@user.org").first()
    space = space_factory("revo", name="撤销空间")
    group = group_factory(
        "临时组", members=["another@user.org"], spaces=[space]
    )

    assert other_client.get("/-/s/revo/home").status_code == 200

    # 撤销成员关系
    UserGroup.query.filter_by(user_id=user.id, group_id=group.id).delete()
    db.session.commit()

    assert other_client.get("/-/s/revo/home").status_code == 404


def test_archived_space_404_for_user_but_admin_ok(
    admin_client, other_client, space_factory, group_factory
):
    group = group_factory("归档组", members=["another@user.org"])
    space = space_factory("old", name="旧空间", authorized_groups=[group])

    assert other_client.get("/-/s/old/home").status_code == 200

    from otterwiki.server import db

    space.is_archived = True
    db.session.commit()

    assert other_client.get("/-/s/old/home").status_code == 404
    assert admin_client.get("/-/s/old/home").status_code == 200


def test_new_registered_user_has_no_access(admin_client, app_with_user):
    """新注册用户不自动加入阅读组，不能访问任何空间。"""
    from otterwiki.auth import SimpleAuth, generate_password_hash
    from otterwiki.server import db
    from datetime import datetime

    user = SimpleAuth.User(
        name="New User",
        email="new@user.org",
        password_hash=generate_password_hash("password1234"),
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        is_admin=False,
        is_approved=True,
        email_confirmed=True,
    )
    db.session.add(user)
    db.session.commit()

    client = app_with_user.test_client()
    result = client.post(
        "/-/login",
        data={"email": "new@user.org", "password": "password1234"},
        follow_redirects=False,
    )
    # 登录成功后跳转首页；未授权用户访问首页被门禁拦截（404）
    assert result.status_code == 302

    assert client.get("/Home").status_code == 404
    assert client.get("/-/search?q=Home").status_code == 404


def test_space_search_scoped(admin_client, space_factory, group_factory):
    """搜索限定在当前空间。"""
    group = group_factory("搜索组")
    space = space_factory("srch", name="搜索空间", authorized_groups=[group])

    from otterwiki.spaces import get_space_storage

    storage = get_space_storage(space)
    storage.store(
        filename="UniqueTermPage.md",
        content="# UniqueTermPage\n\nOnlyInSpaceXyz\n",
        author=("tester", "t@example.org"),
        message="添加",
    )

    response = admin_client.get("/-/s/srch/-/search/OnlyInSpaceXyz")
    assert response.status_code == 200
    html = response.data.decode()
    assert 'href="/-/s/srch/UniqueTermPage"' in html

    # 默认空间中搜索同一词条 -> 无该页面的结果链接
    response = admin_client.get("/-/search/OnlyInSpaceXyz")
    assert response.status_code == 200
    assert 'href="/-/s/srch/UniqueTermPage"' not in response.data.decode()
    assert 'href="/UniqueTermPage"' not in response.data.decode()


def test_space_attachments_scoped(admin_client, space_factory, group_factory):
    """两个空间同名附件互不干扰。"""
    group = group_factory("附件组")
    space_a = space_factory("att1", name="附件甲", authorized_groups=[group])
    space_b = space_factory("att2", name="附件乙", authorized_groups=[group])

    from otterwiki.spaces import get_space_storage

    for space, content in (
        (space_a, b"content-of-a"),
        (space_b, b"content-of-b"),
    ):
        storage = get_space_storage(space)
        storage.store(
            filename="同名文档/data.bin",
            content=content,
            mode="wb",
            author=("tester", "t@example.org"),
            message="添加附件",
        )

    response = admin_client.get("/-/s/att1/同名文档/a/data.bin")
    assert response.data == b"content-of-a"
    response = admin_client.get("/-/s/att2/同名文档/a/data.bin")
    assert response.data == b"content-of-b"


def test_drafts_are_space_scoped(admin_client, space_factory, group_factory):
    """同名页面的草稿按空间隔离。"""
    group = group_factory("草稿组")
    space_a = space_factory("dra", name="草稿甲", authorized_groups=[group])

    # 在空间甲为页面 Home 保存草稿
    response = admin_client.post(
        "/-/s/dra/Home/draft",
        data={"content": "空间甲草稿", "cursor_line": 0, "cursor_ch": 0},
    )
    assert response.status_code == 200

    from otterwiki.models import Drafts, db

    drafts = Drafts.query.filter_by(pagepath="Home").all()
    assert len(drafts) == 1
    assert drafts[0].space_id == space_a.id
    assert drafts[0].content == "空间甲草稿"


def test_space_create_via_admin_ui(admin_client):
    """管理员通过界面创建空间，地址标识固定。"""
    response = admin_client.post(
        "/-/admin/spaces",
        data={
            "name": "产品文档",
            "slug": "product-docs",
            "description": "产品相关",
        },
        follow_redirects=True,
    )
    assert "产品文档" in response.data.decode()

    from otterwiki.spaces import get_space_by_slug

    space = get_space_by_slug("product-docs")
    assert space is not None

    # 重复标识被拒绝
    response = admin_client.post(
        "/-/admin/spaces",
        data={"name": "另一个", "slug": "product-docs"},
        follow_redirects=True,
    )
    assert "已被使用" in response.data.decode()

    # 保留标识被拒绝
    response = admin_client.post(
        "/-/admin/spaces",
        data={"name": "另一个", "slug": "default"},
        follow_redirects=True,
    )
    assert "保留标识" in response.data.decode()


def test_git_http_anonymous_denied(anon_client, app_with_user):
    """匿名 Git HTTP 读取不得绕过文档权限。"""
    from otterwiki.server import app

    app.config["GIT_WEB_SERVER"] = True
    try:
        response = anon_client.get("/.git/info/refs?service=git-upload-pack")
        assert response.status_code == 401
    finally:
        app.config["GIT_WEB_SERVER"] = False


def test_feed_requires_login(anon_client):
    """订阅源纳入空间授权门禁。"""
    response = anon_client.get("/-/changelog/feed.rss")
    assert response.status_code == 302
    assert "/-/login" in response.headers["Location"]


def test_space_switcher_lists_accessible_spaces(
    other_client, space_factory, group_factory
):
    group = group_factory("切换组", members=["another@user.org"])
    space = space_factory(
        "switch", name="可切换空间", authorized_groups=[group]
    )

    response = other_client.get("/Home")
    html = response.data.decode()
    assert "可切换空间" in html
    assert 'href="/-/s/switch/"' in html


def test_index_uses_space_home(admin_client, space_factory, group_factory):
    group = group_factory("首页组")
    space = space_factory("homey", name="首页空间", authorized_groups=[group])
    from otterwiki.spaces import get_space_storage
    from otterwiki.server import db

    storage = get_space_storage(space)
    storage.store(
        filename="门户首页.md",
        content="# 门户首页\n\n空间自定义首页内容。\n",
        author=("tester", "t@example.org"),
        message="添加",
    )
    space.home_page = "门户首页"
    db.session.commit()

    response = admin_client.get("/-/s/homey/")
    assert response.status_code == 200
    assert "空间自定义首页内容" in response.data.decode()


def test_space_switcher_offers_default_space_at_root(
    admin_client, space_factory, group_factory
):
    """空间页面里能切回默认空间：默认空间固定在站点根 "/"，
    不能被 SCRIPT_NAME 前缀污染成当前空间地址。"""
    group = group_factory("回切组", members=["mail@example.org"])
    space_factory("rootback", name="回切空间", authorized_groups=[group])

    response = admin_client.get("/-/s/rootback/")
    assert response.status_code == 200
    html = response.data.decode()
    # 切换下拉中“主空间（default）”入口必须是站点根
    assert 'href="/"' in html
    assert 'href="/-/s/rootback/"' in html


def test_user_management_lists_member_groups(admin_client, group_factory):
    """用户管理页表格包含所属组列，展示该用户加入的用户组。"""
    group = group_factory("展示组", members=["another@user.org"])
    _ = group
    response = admin_client.get("/-/admin/user_management")
    assert response.status_code == 200
    html = response.data.decode()
    assert "所属组" in html
    assert "默认阅读组" in html
