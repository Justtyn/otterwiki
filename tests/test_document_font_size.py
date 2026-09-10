#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""全局文档字号（DOCUMENT_FONT_SIZE）测试。"""

import pytest


def test_normalize_document_font_size():
    from otterwiki.util import normalize_document_font_size

    assert normalize_document_font_size("15") == 15
    assert normalize_document_font_size(12) == 12
    assert normalize_document_font_size(24) == 24
    assert normalize_document_font_size(" 16 ") == 16
    assert normalize_document_font_size("11") is None
    assert normalize_document_font_size("25") is None
    assert normalize_document_font_size("abc") is None
    assert normalize_document_font_size("") is None
    assert normalize_document_font_size(None) is None
    # 非法值可以带回退默认
    assert normalize_document_font_size("abc", default=15) == 15


def test_default_font_size_is_15(admin_client):
    """默认字号 15px，页面注入 CSS 变量。"""
    response = admin_client.get("/Home")
    html = response.data.decode()
    assert "--otterwiki-document-font-size: 15px;" in html


def test_font_size_save_and_take_effect(admin_client):
    """保存后从下一次页面请求生效。"""
    from otterwiki.preferences import get_document_font_size

    response = admin_client.post(
        "/-/admin/content_and_editing",
        data={"document_font_size": "18"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert get_document_font_size() == 18

    html = admin_client.get("/Home").data.decode()
    assert "--otterwiki-document-font-size: 18px;" in html


def test_font_size_invalid_rejected(admin_client):
    """表单非法值拒绝保存。"""
    from otterwiki.models import Preferences

    response = admin_client.post(
        "/-/admin/content_and_editing",
        data={"document_font_size": "300"},
        follow_redirects=True,
    )
    assert "12 到 24" in response.data.decode()
    entry = Preferences.query.filter_by(name="DOCUMENT_FONT_SIZE").first()
    assert entry is None


def test_font_size_boundary_values(admin_client):
    from otterwiki.models import Preferences

    for value, expect in (("12", 12), ("24", 24)):
        admin_client.post(
            "/-/admin/content_and_editing",
            data={"document_font_size": value},
            follow_redirects=True,
        )
        entry = Preferences.query.filter_by(name="DOCUMENT_FONT_SIZE").first()
        assert entry.value == str(expect)


def test_font_size_restore_default(admin_client):
    """恢复默认：删除保存值，回退配置默认。"""
    from otterwiki.models import Preferences
    from otterwiki.preferences import get_document_font_size
    from otterwiki.server import app
    from otterwiki.util import normalize_document_font_size

    admin_client.post(
        "/-/admin/content_and_editing",
        data={"document_font_size": "20"},
        follow_redirects=True,
    )
    assert get_document_font_size() == 20

    response = admin_client.post(
        "/-/admin/content_and_editing",
        data={"document_font_size": "20", "document_font_size_reset": "1"},
        follow_redirects=True,
    )
    assert "已恢复默认文档字号" in response.data.decode()
    assert (
        Preferences.query.filter_by(name="DOCUMENT_FONT_SIZE").first() is None
    )
    # 回退到配置文件/环境变量的默认值
    assert get_document_font_size() == normalize_document_font_size(
        app.config.get("DOCUMENT_FONT_SIZE"), default=15
    )


def test_font_size_db_value_does_not_override_config_default(admin_client):
    """update_app_config 不把 DB 保存值写进 app.config（保持配置默认语义）。"""
    from otterwiki.models import Preferences
    from otterwiki.server import app, db, update_app_config

    db.session.add(Preferences(name="DOCUMENT_FONT_SIZE", value="22"))
    db.session.commit()
    update_app_config()
    assert app.config["DOCUMENT_FONT_SIZE"] != "22"


def test_font_size_editor_not_affected(admin_client):
    """编辑器页面同样注入变量，但只有 .page 作用域消费它。"""
    html = admin_client.get("/Home/edit").data.decode()
    assert "--otterwiki-document-font-size: 15px;" in html


@pytest.mark.parametrize("width", [600, 1440])
def test_document_computed_sizes_in_browser(tmp_path, width):
    """用真实浏览器检查移动端和桌面的字号，缺少浏览器时明确跳过。"""
    import html
    import json
    import os
    import re
    import shutil
    import subprocess
    from pathlib import Path

    from browser_support import browser_flags, require_chrome

    chrome = require_chrome("验证实际排版字号")
    css_root = Path(__file__).resolve().parents[1] / "otterwiki/static/css"
    css = (css_root / "halfmoon.min.css").read_text()
    css += (css_root / "elements/page.css").read_text()
    css += (css_root / "portal.css").read_text()
    page = tmp_path / "font-check.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><style>' + css + '</style>'
        '<body class="portal-theme"><nav id="nav">导航</nav>'
        '<div class="card"><h2 id="admin" class="card-title">管理</h2></div>'
        '<textarea id="editor">编辑器</textarea>'
        '<article class="page"><p id="text">正文</p>'
        '<h1 id="h1">标题一</h1><h2 id="h2">标题二</h2>'
        '<h3 id="h3">标题三</h3><h4 id="h4">标题四</h4>'
        '<h5 id="h5">标题五</h5><h6 id="h6">标题六</h6>'
        '<code id="code">代码</code><table id="table"><tr><td>表格</td>'
        '</tr></table></article><pre id="result"></pre>'
        '<script>const results = [12,15,24].map(size => {'
        'document.body.style.setProperty("--otterwiki-document-font-size",size+"px");'
        'const sizes = Object.fromEntries(["text","h1","h2","h3","h4","h5","h6",'
        '"code","table","nav","admin","editor"].map(id => '
        '[id,parseFloat(getComputedStyle(document.getElementById(id)).fontSize)]));'
        'return {size,width:innerWidth,sizes};});'
        'document.getElementById("result").textContent=JSON.stringify(results);'
        '</script></body>'
    )
    result = subprocess.run(
        [
            chrome,
            *browser_flags(),
            "--no-default-browser-check",
            f"--user-data-dir={tmp_path / 'chrome-profile'}",
            f"--window-size={width},900",
            "--dump-dom",
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout)
    assert match, result.stdout
    readings = json.loads(html.unescape(match.group(1)))
    default = readings[1]["sizes"]
    assert default["h1"] == pytest.approx(26 if width < 768 else 32, abs=0.01)
    assert default["h2"] == pytest.approx(20 if width < 768 else 23, abs=0.01)
    for reading in readings:
        size, sizes = reading["size"], reading["sizes"]
        assert reading["width"] == width
        assert sizes["text"] == size
        assert sizes["h1"] > sizes["h2"] > sizes["h3"] > sizes["h4"]
        assert sizes["h4"] > sizes["h5"] == sizes["h6"]
        for key in ("h1", "h2", "h3", "h4", "h5", "h6", "code", "table"):
            assert sizes[key] == pytest.approx(
                default[key] * size / 15, abs=0.01
            )
        for key in ("nav", "admin", "editor"):
            assert sizes[key] == default[key]
