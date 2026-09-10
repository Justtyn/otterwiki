#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""浏览器测试共用工具。

受限环境（容器、CI、无用户命名空间）里 Chromium 沙箱无法初始化，测试用
``--no-sandbox`` 只是渲染本地临时页面，不接触任何远程内容；缺少浏览器时
默认明确 skip，设置 ``OTTERWIKI_REQUIRE_BROWSER_TESTS=1`` 后则直接失败，
避免「静默零覆盖」。
"""

import os
from pathlib import Path

import pytest


def browser_flags() -> list[str]:
    """启动 headless Chrome 的公共参数。"""
    return [
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--no-first-run",
        "--disable-background-networking",
    ]


def find_chrome() -> str | None:
    candidates = [
        os.environ.get("CHROME_BIN"),
        *(
            str(p)
            for p in sorted(
                (Path.home() / "Library/Caches/ms-playwright").glob(
                    "chromium_headless_shell-*/chrome-headless-shell-*/"
                    "chrome-headless-shell"
                ),
                reverse=True,
            )
        ),
    ]
    for name in (
        "chrome-headless-shell",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        found = _which(name)
        if found:
            candidates.append(found)
    candidates.append(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def require_chrome(purpose: str) -> str:
    chrome = find_chrome()
    if chrome:
        return chrome
    message = f"需要 Chrome/Chromium{purpose}"
    if os.environ.get("OTTERWIKI_REQUIRE_BROWSER_TESTS") in (
        "1",
        "true",
        "yes",
    ):
        pytest.fail(message + "（OTTERWIKI_REQUIRE_BROWSER_TESTS=1）")
    pytest.skip(message)
