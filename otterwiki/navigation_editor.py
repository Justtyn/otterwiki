#!/usr/bin/env python

"""Admin UI and persistence for editable structured documentation navigation."""

from __future__ import annotations

import json
import re
from typing import Any

from flask import abort, redirect, render_template, url_for

from otterwiki.auth import get_author, has_permission
from otterwiki.helper import toast
from otterwiki.server import storage
from otterwiki.structured_navigation import (
    StructuredNavigation,
    _clean_repo_path,
    clear_structured_navigation_cache,
    structured_navigation_namespaces,
)

SECTIONS = (
    ("manual", "产品手册"),
    ("components", "产品组件"),
    ("reference", "参考指南"),
)
SECTION_KEYS = {key for key, _ in SECTIONS}
ID_PATTERN = re.compile(r"^[\w:./-]{1,500}$", re.UNICODE)
MAX_TABS = 20
MAX_NODES = 5000


class NavigationEditorError(ValueError):
    pass


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _text(value: Any, *, maximum: int, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise NavigationEditorError(f"{field}不能超过 {maximum} 个字符。")
    return text


def _path(value: Any) -> str:
    raw = _text(value, maximum=500, field="页面路径")
    return _clean_repo_path(raw) if raw else ""


def _order(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def navigation_editor_form(namespace: str = "", section: str = "components"):
    if not has_permission("ADMIN"):
        abort(403)

    namespaces = structured_navigation_namespaces()
    namespace = _clean_repo_path(namespace)
    if not namespace and namespaces:
        namespace = namespaces[0]
    if namespace and namespace not in namespaces:
        abort(404)
    if section not in SECTION_KEYS:
        section = "components"

    navigation = (
        StructuredNavigation.for_namespace(namespace) if namespace else None
    )
    tree = navigation.editable_tree(section) if navigation else None
    rows = StructuredNavigation.flatten_tree(tree) if tree is not None else []
    tabs = navigation.tab_config() if navigation else []

    return render_template(
        "admin/navigation.html",
        title="文档导航编辑",
        namespaces=namespaces,
        namespace=namespace,
        sections=SECTIONS,
        section=section,
        navigation_title=tree.title if tree is not None else "文档目录",
        tabs=tabs,
        navigation_rows=rows,
    )


def _validate_tabs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise NavigationEditorError("分类标签数据格式不正确。")
    if len(value) > MAX_TABS:
        raise NavigationEditorError(f"分类标签最多允许 {MAX_TABS} 个。")

    tabs = []
    used_keys = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), maximum=120, field="标签名称")
        if not title:
            continue
        key = _text(
            item.get("key") or f"custom-{index + 1}",
            maximum=120,
            field="标签标识",
        )
        if not ID_PATTERN.fullmatch(key) or key in used_keys:
            raise NavigationEditorError("分类标签标识重复或包含无效字符。")
        used_keys.add(key)
        path = _path(item.get("path"))
        tabs.append(
            {
                "key": key,
                "title": title,
                "path": path,
                "visible": _as_bool(item.get("visible"), True),
                "clickable": _as_bool(item.get("clickable"), True)
                and bool(path),
            }
        )
    return tabs


def _validate_nodes(
    navigation: StructuredNavigation, section: str, value: Any
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(value, list):
        raise NavigationEditorError("目录项数据格式不正确。")
    if len(value) > MAX_NODES:
        raise NavigationEditorError(f"目录项最多允许 {MAX_NODES} 个。")

    base_rows = StructuredNavigation.flatten_tree(
        navigation.base_tree(section)
    )
    source_ids = {row["id"] for row in base_rows}
    nodes: dict[str, dict[str, Any]] = {}
    custom: list[dict[str, Any]] = []
    custom_ids = set()

    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        node_id = _text(item.get("id"), maximum=500, field="目录项标识")
        if not node_id or not ID_PATTERN.fullmatch(node_id):
            raise NavigationEditorError("目录项标识为空或包含无效字符。")
        title = _text(item.get("title"), maximum=200, field="目录项名称")
        if not title:
            raise NavigationEditorError("目录项名称不能为空。")
        common = {
            "title": title,
            "visible": _as_bool(item.get("visible"), True),
            "clickable": _as_bool(item.get("clickable"), True),
            "order": _order(item.get("order"), index),
        }
        if node_id in source_ids:
            nodes[node_id] = common
            continue

        if not node_id.startswith("custom:") or node_id in custom_ids:
            raise NavigationEditorError("自定义目录项标识重复或无效。")
        custom_ids.add(node_id)
        parent = _text(item.get("parent"), maximum=500, field="上级目录项")
        path = _path(item.get("path"))
        custom.append(
            {
                "id": node_id,
                "parent": parent,
                "title": title,
                "path": path,
                "visible": common["visible"],
                "clickable": common["clickable"] and bool(path),
                "order": common["order"],
            }
        )

    valid_parents = source_ids | custom_ids | {""}
    parents = {item["id"]: item["parent"] for item in custom}
    for item in custom:
        if item["parent"] not in valid_parents:
            raise NavigationEditorError(
                f"“{item['title']}”选择了不存在的上级目录项。"
            )
        seen = {item["id"]}
        parent = item["parent"]
        while parent in parents:
            if parent in seen:
                raise NavigationEditorError("自定义目录项不能循环嵌套。")
            seen.add(parent)
            parent = parents[parent]

    return nodes, custom


def save_navigation_editor(form):
    if not has_permission("ADMIN"):
        abort(403)

    namespace = _clean_repo_path(form.get("namespace", ""))
    section = form.get("section", "components")
    if section not in SECTION_KEYS:
        raise NavigationEditorError("文档分类无效。")
    navigation = StructuredNavigation.for_namespace(namespace)
    if navigation is None:
        abort(404)

    try:
        payload = json.loads(form.get("navigation_payload", ""))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise NavigationEditorError(
            "导航编辑数据无法解析，请刷新后重试。"
        ) from error
    if not isinstance(payload, dict):
        raise NavigationEditorError("导航编辑数据格式不正确。")

    title = _text(payload.get("title"), maximum=120, field="目录标题")
    tabs = _validate_tabs(payload.get("tabs", []))
    nodes, custom = _validate_nodes(
        navigation, section, payload.get("nodes", [])
    )

    config = dict(navigation.override)
    config["version"] = 1
    config["title"] = title or "文档目录"
    config["tabs"] = tabs
    sections = config.get("sections", {})
    if not isinstance(sections, dict):
        sections = {}
    sections[section] = {"nodes": nodes, "custom": custom}
    config["sections"] = sections

    filename = f"{namespace}/.navigation.json"
    storage.store(
        filename,
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        message=f"更新 {namespace} 文档导航",
        author=get_author(),
    )
    clear_structured_navigation_cache()
    toast("文档导航已保存。")
    return redirect(
        url_for("admin_navigation", namespace=namespace, section=section)
    )
