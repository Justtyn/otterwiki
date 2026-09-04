#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

import pytest


def save_shortcut(test_client, pagename, content):
    rv = test_client.post(
        "/{}/save".format(pagename),
        data={
            "content": content,
            "commit": "test",
        },
        follow_redirects=True,
    )
    assert rv.status_code == 200


def _login_client(app):
    """多空间权限：匿名无法访问内容。确保存在默认管理员（管理员绕过空间
    组授权）并返回已登录客户端。自包含实现：全量回归时 docs/ 下另有
    conftest.py，跨目录 import conftest 会产生模块名冲突。"""
    import re as _re
    from datetime import datetime

    from otterwiki.auth import SimpleAuth, generate_password_hash
    from otterwiki.server import db
    from otterwiki.spaces import ensure_default_space

    with app.app_context():
        ensure_default_space()
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
    client = app.test_client()
    login_html = client.get("/-/login").data.decode()
    m = _re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_html)
    assert m is not None, "登录页缺少 csrf_token"
    client.post(
        "/-/login",
        data={
            "email": "mail@example.org",
            "password": "password1234",
            "csrf_token": m.group(1),
        },
    )
    return client


def app_with_default_backlink_settings(create_app):
    create_app.config["WIKILINK_STYLE"] = ""
    create_app.config["RETAIN_PAGE_NAME_CASE"] = False
    return create_app


@pytest.fixture
def test_client(app_with_default_backlink_settings):
    # 多空间权限：匿名无法访问内容，登录默认管理员
    return _login_client(app_with_default_backlink_settings)


@pytest.mark.parametrize(
    "wikilink_style,content,expected",
    [
        (
            "",
            "[[Target Title|Path/OldPage]]",
            "[[Target Title|Path/NewPage]]",
        ),
        (
            "LINKTITLE",
            "[[Path/OldPage|Target Title]]",
            "[[Path/NewPage|Target Title]]",
        ),
    ],
)
def test_rename_backlinks_wikilink_style(
    create_app,
    wikilink_style,
    content,
    expected,
):
    app = create_app
    saved = app.config["WIKILINK_STYLE"]
    app.config["WIKILINK_STYLE"] = wikilink_style

    content += "\n"
    expected += "\n"

    # 多空间权限：匿名无法访问内容，登录默认管理员
    with _login_client(app) as client:
        save_shortcut(client, "example", content)

        from otterwiki.backlinks import rename_backlinks

        result = rename_backlinks(
            "Path/OldPage",
            "Path/NewPage",
        )

        assert result == {
            "example.md": expected,
        }

        updated = app.storage.load("example.md")
        assert updated == expected

    app.config["WIKILINK_STYLE"] = saved


@pytest.mark.parametrize(
    "retain_case,content,expected",
    [
        (
            False,
            "![](/path/oldpage/image.png)",
            "![](/Path/NewPage/image.png)",
        ),
        (
            True,
            "![](/path/oldpage/image.png)",
            "![](/path/oldpage/image.png)",
        ),
    ],
)
def test_rename_backlinks_retain_page_name_case(
    create_app,
    retain_case,
    content,
    expected,
):
    app = create_app
    saved = app.config["RETAIN_PAGE_NAME_CASE"]
    app.config["RETAIN_PAGE_NAME_CASE"] = retain_case

    content += "\n"
    expected += "\n"

    # 多空间权限：匿名无法访问内容，登录默认管理员
    with _login_client(app) as client:
        save_shortcut(client, "example", content)

        from otterwiki.backlinks import rename_backlinks

        result = rename_backlinks(
            "Path/OldPage",
            "Path/NewPage",
        )

        if expected != content:
            assert result == {
                "example.md": expected,
            }
        else:
            assert result == {}

        assert app.storage.load("example.md") == expected

    app.config["RETAIN_PAGE_NAME_CASE"] = saved


@pytest.mark.parametrize(
    "old_page,new_page,content,expected",
    [
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[[Pathto/Targetpage]]",
            "[[Pathto/Renamedpage]]",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[[Target Page|Pathto/Targetpage]]",
            "[[Target Page|Pathto/Renamedpage]]",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[Description](/Pathto/Targetpage)",
            "[Description](/Pathto/Renamedpage)",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[[Pathto/Targetpage#sub-heading]]",
            "[[Pathto/Renamedpage#sub-heading]]",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[[Target Page With Anchor|Pathto/Targetpage#sub-heading]]",
            "[[Target Page With Anchor|Pathto/Renamedpage#sub-heading]]",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[Anchor](/Pathto/Targetpage#sub-heading)",
            "[Anchor](/Pathto/Renamedpage#sub-heading)",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "[Otter](/Pathto/Targetpage/otter.png)",
            "[Otter](/Pathto/Renamedpage/otter.png)",
        ),
        (
            "Pathto/Targetpage",
            "Pathto/Renamedpage",
            "![](/Pathto/Targetpage/otter.png)",
            "![](/Pathto/Renamedpage/otter.png)",
        ),
        (
            "Path With Spaces/Target Page",
            "Path With Spaces/Renamed Page",
            "[Description](/Path%20With%20Spaces/Target%20Page)",
            "[Description](/Path%20With%20Spaces/Renamed%20Page)",
        ),
    ],
)
def test_rename_backlinks_supported_links(
    create_app,
    old_page,
    new_page,
    content,
    expected,
):
    app = create_app

    content += "\n"
    expected += "\n"

    # 多空间权限：匿名无法访问内容，登录默认管理员
    with _login_client(app) as client:
        save_shortcut(client, "example", content)

        from otterwiki.backlinks import rename_backlinks

        result = rename_backlinks(
            old_page,
            new_page,
        )

        assert result == {
            "example.md": expected,
        }

        assert app.storage.load("example.md") == expected
