#!/usr/bin/env python

import json

from bs4 import BeautifulSoup


def _populate_structured_docs(storage):
    files = {
        "suite/.portal.yml": """mappings:
  - doc_code: demo-component
    file_path: docs/components/demo
""",
        "suite/.idp/.model.yaml": """- domainName: Demo
  code: demo
  modules:
    - moduleName: 开发平台
      code: development
      components:
        - componentName: 示例组件
          code: demo-component
""",
        "suite/aps/index.md": "# 产品手册\n",
        "suite/components/index.md": "# 产品组件\n",
        "suite/reference/index.md": "# 参考指南\n",
        "suite/reference/config/framework/1-full.md": "# 全量配置清单\n",
        "suite/reference/apidocs/index.html": "<h1>API Docs</h1>",
        "suite/reference/assets/logo.png": "not-a-real-image",
        "suite/components/demo/index.md": """---
title: 组件首页
---
""",
        "suite/components/demo/.sidebar.json.bak": json.dumps(
            [
                {
                    "title": "演示",
                    "path": "demo",
                    "children": [
                        {"title": "第二页", "path": "demo/two"},
                        {"title": "第一页", "path": "demo/one"},
                    ],
                },
                {"title": "不可见", "path": "hidden", "hide": True},
            ],
            ensure_ascii=False,
        ),
        "suite/components/demo/demo/one.md": """---
title: 首篇
nav_weight: 99
---

## 依赖

正文。

## 配置
""",
        "suite/components/demo/demo/two.md": """---
title: 次篇
nav_weight: 1
---
""",
        "suite/components/demo/extra.md": """---
title: 附加页面
nav_weight: 5
---
""",
        "suite/components/demo/hidden.md": "# 隐藏页面\n",
    }
    for filename, content in files.items():
        storage.update(filename, content)
    storage.commit(
        list(files),
        message="structured documentation fixture",
        author=("Test", "test@example.org"),
        no_add=True,
    )


def test_rule_driven_navigation_honors_model_portal_and_sidebar(
    create_app, req_ctx
):
    from otterwiki.sidebar import SidebarPageIndex

    _populate_structured_docs(create_app.storage)
    tree = SidebarPageIndex("suite/components/demo/demo/one").query()

    assert tree.structured is True
    assert [(tab.title, tab.active) for tab in tree.tabs] == [
        ("产品手册", False),
        ("产品组件", True),
        ("参考指南", False),
    ]

    module = next(iter(tree.values()))
    component = next(iter(module.children.values()))
    assert (module.number, module.header) == ("1", "开发平台")
    assert (component.number, component.header) == ("1.1", "示例组件")

    children = list(component.children.values())
    assert [entry.header for entry in children] == ["演示", "附加页面"]
    assert children[0].linkable is True
    assert children[0].path == "suite/components/demo/demo/two"
    assert "不可见" not in [entry.header for entry in children]
    demo_children = list(children[0].children.values())
    # Explicit sidebar ordering wins over frontmatter nav_weight.
    assert [entry.header for entry in demo_children] == ["第二页", "第一页"]
    assert [entry.number for entry in demo_children] == ["1.1.1.1", "1.1.1.2"]
    assert demo_children[1].active is True


def test_directory_without_index_links_to_first_real_descendant(
    create_app, req_ctx
):
    from otterwiki.sidebar import SidebarPageIndex

    _populate_structured_docs(create_app.storage)
    tree = SidebarPageIndex("suite/reference/index").query()

    config = next(entry for entry in tree.values() if entry.header == "config")
    assert config.linkable is True
    assert config.path == "suite/reference/config/framework/1-full"
    entries = {entry.header: entry for entry in tree.values()}
    assert entries["apidocs"].path == "suite/reference/apidocs/index.html"
    assert entries["apidocs"].linkable is True
    assert "assets" not in entries


def test_windows_directory_landing_path_uses_url_separators(
    create_app, req_ctx, monkeypatch
):
    from otterwiki.structured_navigation import StructuredNavigation

    navigation = StructuredNavigation.__new__(StructuredNavigation)
    monkeypatch.setattr(
        navigation,
        "_directory_landing_file",
        lambda path: r"suite/components/demo\index.md",
    )
    monkeypatch.setattr(create_app.storage, "isdir", lambda path: True)

    assert (
        navigation._landing_path("suite/components/demo")
        == "suite/components/demo/index"
    )


def test_structured_document_renders_tabs_numbers_and_numbered_toc(
    create_app, test_client
):
    _populate_structured_docs(create_app.storage)

    response = test_client.get("/suite/components/demo/demo/one")
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")

    assert [
        link.get_text(strip=True)
        for link in soup.select(".documentation-tabs a")
    ] == ["产品手册", "产品组件", "参考指南"]
    assert (
        soup.select_one(".documentation-tabs a.active").get_text(strip=True)
        == "产品组件"
    )
    assert soup.select_one(".structured-menutree") is not None
    assert soup.select_one("article.structured-doc-page") is not None
    assert soup.select_one("article h1").get_text(" ", strip=True) == "1 首篇"
    assert [
        link.get_text(" ", strip=True)
        for link in soup.select("#extranav-toc a")
    ] == ["1 首篇", "1.1 依赖", "1.2 配置"]
    assert soup.select_one("article #frontmatter") is not None


def test_heading_numbering_preserves_non_heading_html_and_existing_numbers():
    from otterwiki.structured_navigation import number_document_headings

    html = (
        '<h1 id="overview">概览</h1>'
        '<table data-example="unchanged"><tr><td>A &amp; B</td></tr></table>'
        '<h2 class="section" id="details">1.1 已编号</h2>'
    )
    toc = [
        (1, "概览", 1, "概览", "overview"),
        (2, "1.1 已编号", 2, "1.1 已编号", "details"),
    ]

    rendered, numbered_toc = number_document_headings(html, toc)

    assert rendered == (
        '<h1 id="overview"><span class="heading-number">1 </span>概览</h1>'
        '<table data-example="unchanged"><tr><td>A &amp; B</td></tr></table>'
        '<h2 class="section" id="details">1.1 已编号</h2>'
    )
    assert [entry[3] for entry in numbered_toc] == ["1 概览", "1.1 已编号"]


def test_frontmatter_title_is_used_by_regular_page_index(create_app, req_ctx):
    from otterwiki.sidebar import SidebarPageIndex

    create_app.storage.store(
        "frontmatter-page.md",
        "---\ntitle: 元数据标题\n---\n\n正文\n",
        author=("Test", "test@example.org"),
    )
    create_app.config["SIDEBAR_MENUTREE_FOCUS"] = "OFF"

    tree = SidebarPageIndex("/").query()
    assert tree["frontmatter-page"].header == "元数据标题"


def test_complete_tree_is_available_before_visiting_child_pages(
    create_app, req_ctx
):
    from otterwiki.sidebar import SidebarPageIndex

    _populate_structured_docs(create_app.storage)
    tree = SidebarPageIndex("suite/components/index").query()

    module = next(iter(tree.values()))
    component = next(iter(module.children.values()))
    assert [entry.header for entry in component.children.values()] == [
        "演示",
        "附加页面",
    ]
    demo = next(iter(component.children.values()))
    assert [entry.header for entry in demo.children.values()] == [
        "第二页",
        "第一页",
    ]


def test_page_metadata_is_read_once_per_navigation_build(
    create_app, req_ctx, monkeypatch
):
    from otterwiki.structured_navigation import StructuredNavigation

    _populate_structured_docs(create_app.storage)
    navigation = StructuredNavigation.for_page(
        "suite/components/demo/demo/one"
    )
    assert navigation is not None
    original_load = create_app.storage.load
    page_reads: dict[str, int] = {}

    def tracked_load(filename, *args, **kwargs):
        if filename.endswith(".md"):
            page_reads[filename] = page_reads.get(filename, 0) + 1
        return original_load(filename, *args, **kwargs)

    monkeypatch.setattr(create_app.storage, "load", tracked_load)
    navigation._build_component_tree(expand_all=True)

    assert page_reads
    assert max(page_reads.values()) == 1


def test_complete_tree_cache_is_reused_and_invalidated_by_commit(
    create_app, req_ctx
):
    from otterwiki.sidebar import SidebarPageIndex
    from otterwiki.structured_navigation import (
        clear_structured_navigation_cache,
        structured_navigation_cache_info,
    )

    _populate_structured_docs(create_app.storage)
    clear_structured_navigation_cache()

    first = SidebarPageIndex("suite/components/index").query()
    after_first = structured_navigation_cache_info()
    second = SidebarPageIndex("suite/components/demo/demo/one").query()
    after_second = structured_navigation_cache_info()

    assert after_first.misses == 1
    assert after_second.hits == after_first.hits + 1
    assert first is not second

    create_app.storage.store(
        "suite/components/demo/new.md",
        "# 新页面\n",
        author=("Test", "test@example.org"),
    )
    refreshed = SidebarPageIndex("suite/components/index").query()
    after_commit = structured_navigation_cache_info()
    component = next(iter(next(iter(refreshed.values())).children.values()))

    assert after_commit.misses == after_second.misses + 1
    assert "新页面" in [entry.header for entry in component.children.values()]
