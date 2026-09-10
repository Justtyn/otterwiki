#!/usr/bin/env python

"""Rule-driven documentation navigation for imported product manuals.

The supported rule set mirrors the APStack documentation repository:

* ``.idp/.model.yaml`` defines domains, modules, and components.
* ``.portal.yml`` maps component codes to documentation directories.
* ``.sidebar.json`` (with ``.sidebar.json.bak`` as a legacy fallback)
  defines titles, ordering, nesting, and hidden entries inside a directory.
* Markdown front matter supplies ``title``, ``nav_weight``/``order``, and
  optional visibility flags.

The module intentionally returns the same entry shape as the regular
``SidebarPageIndex`` so existing templates and plugins remain compatible.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import posixpath
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from otterwiki.gitstorage import StorageError
from otterwiki.server import app, storage
from otterwiki.util import (
    _as_bool,
    get_frontmatter,
    get_header,
    join_path,
    split_path,
)

SIDEBAR_FILENAMES = (
    ".sidebar.json",
    ".sidebar.zh-cn.json",
    ".sidebar.zh_CN.json",
    ".sidebar.json.bak",
)
INDEX_FILENAMES = ("index.md", "readme.md")
HTML_INDEX_FILENAMES = ("index.html", "index.htm")
NUMBER_PREFIX = re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:[、.]|\s)+")
HEADING_ELEMENT = re.compile(
    r"(?P<open><h(?P<level>[1-6])\b[^>]*>)"
    r"(?P<body>.*?)"
    r"(?P<close></h(?P=level)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
HEADING_ID = re.compile(
    r"\bid\s*=\s*(?P<quote>['\"])(?P<anchor>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


def _natural_sort_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


@dataclass
class NavigationTab:
    key: str
    title: str
    path: str
    active: bool = False
    visible: bool = True
    clickable: bool = True


@dataclass
class NavigationEntry:
    path: str
    header: str
    node_id: str = ""
    scope: str = ""
    children: OrderedDict[str, "NavigationEntry"] = field(
        default_factory=OrderedDict
    )
    number: str = ""
    linkable: bool = True
    active: bool = False
    configured: bool = False
    weight: float = 1000.0
    visible: bool = True
    custom: bool = False


class NavigationTree(OrderedDict[str, NavigationEntry]):
    """Ordered tree with presentation metadata consumed by Jinja."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.structured = False
        self.title = "页面索引"
        self.tabs: list[NavigationTab] = []
        self.number_headings = False
        self.namespace = ""
        self.section = ""


def _clean_repo_path(value: str) -> str:
    """Normalise a repository path used by navigation rules.

    与 ``sanitize_pagename`` 不同，这里**保留点号**：APStack 文档大量使用
    ``v1.2``、``1.0`` 这类版本号目录，删除点号会让规则指向不存在的路径
    （``v1.2/guide`` 曾会被改写成 ``v12/guide``）。仅拦截真正危险的写法：
    空字符、绝对路径、``.``/``..`` 路径段与反斜杠分隔符。
    """
    value = str(value).replace("\\", "/")
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\.md$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[?$!#\x00]", "", value)
    value = value.strip().strip("/")
    segments = []
    for segment in value.split("/"):
        segment = segment.strip()
        if not segment or segment in (".", ".."):
            # 相对回溯与空段一律丢弃，避免越出命名空间
            continue
        segments.append(segment)
    value = "/".join(segments)
    if not app.config["RETAIN_PAGE_NAME_CASE"]:
        value = value.lower()
    return value


def _safe_load_yaml(filename: str) -> Any:
    try:
        return yaml.safe_load(storage.load(filename))
    except (StorageError, TypeError, ValueError, yaml.YAMLError) as error:
        app.logger.warning(
            "Unable to parse structured navigation YAML %s: %s",
            filename,
            error,
        )
        return None


def _safe_load_json(filename: str) -> Any:
    try:
        return json.loads(storage.load(filename))
    except (
        StorageError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        app.logger.warning(
            "Unable to parse structured navigation JSON %s: %s",
            filename,
            error,
        )
        return None


def _is_page_active(pagepath: str, target: str) -> bool:
    page = _clean_repo_path(pagepath)
    target = _clean_repo_path(target)
    return page == target or page.startswith(target.rstrip("/") + "/")


class StructuredNavigation:
    def __init__(self, namespace: str, pagepath: str):
        self.namespace = _clean_repo_path(namespace)
        self.pagepath = _clean_repo_path(pagepath)
        self._page_cache: dict[str, tuple[str | None, float, bool]] = {}
        self._sidebar_cache: dict[str, list[dict[str, Any]]] = {}
        self._directory_cache: dict[str, tuple[list[str], list[str]]] = {}
        self._landing_cache: dict[str, str | None] = {}
        self.portal_path = f"{self.namespace}/.portal.yml"
        self.model_path = f"{self.namespace}/.idp/.model.yaml"
        self.override_path = f"{self.namespace}/.navigation.json"
        self.portal = _safe_load_yaml(self.portal_path)
        self.model = _safe_load_yaml(self.model_path)
        self.override = self._load_override()
        self.mappings = self._load_mappings()

    def _load_override(self) -> dict[str, Any]:
        if not storage.exists(self.override_path):
            return {}
        value = _safe_load_json(self.override_path)
        return value if isinstance(value, dict) else {}

    @classmethod
    def for_page(cls, pagepath: str) -> "StructuredNavigation | None":
        parts = split_path(_clean_repo_path(pagepath))
        if not parts:
            return None
        namespace = parts[0]
        if not (
            storage.exists(f"{namespace}/.portal.yml")
            and storage.exists(f"{namespace}/.idp/.model.yaml")
        ):
            return None
        navigation = cls(namespace, pagepath)
        if not isinstance(navigation.portal, dict) or not isinstance(
            navigation.model, list
        ):
            return None
        return navigation

    @classmethod
    def for_namespace(
        cls, namespace: str, pagepath: str | None = None
    ) -> "StructuredNavigation | None":
        namespace = _clean_repo_path(namespace)
        if not namespace or not (
            storage.exists(f"{namespace}/.portal.yml")
            and storage.exists(f"{namespace}/.idp/.model.yaml")
        ):
            return None
        navigation = cls(namespace, pagepath or namespace)
        if not isinstance(navigation.portal, dict) or not isinstance(
            navigation.model, list
        ):
            return None
        return navigation

    def _legacy_path_to_repository(self, path: str) -> str:
        path = path.replace("\\", "/").strip("/")
        if path.lower().startswith("docs/"):
            path = path[5:]
        return join_path([self.namespace, _clean_repo_path(path)])

    def _load_mappings(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(self.portal, dict):
            return result
        mappings = self.portal.get("mappings", [])
        if not isinstance(mappings, list):
            return result
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            code = mapping.get("doc_code")
            file_path = mapping.get("file_path")
            if not isinstance(code, str) or not isinstance(file_path, str):
                continue
            repository_path = self._legacy_path_to_repository(file_path)
            if storage.isdir(repository_path) or storage.exists(
                repository_path + ".md"
            ):
                result[code] = repository_path
        return result

    def _landing_path(self, path: str) -> str:
        path = _clean_repo_path(path)
        if storage.isdir(path):
            landing_file = self._directory_landing_file(path)
            if landing_file is not None:
                landing_path = (
                    landing_file[:-3]
                    if landing_file.lower().endswith(".md")
                    else landing_file
                )
                # ``join_path`` follows the host filesystem and therefore
                # inserts backslashes on Windows.  Navigation paths are URL
                # paths and must always use forward slashes; otherwise Flask
                # quotes the separator as ``%5C`` and the link returns 404.
                return landing_path.replace("\\", "/")
        if storage.exists(path + ".md"):
            return path
        return path

    def default_tabs(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "manual",
                "title": "产品手册",
                "path": f"{self.namespace}/aps",
                "visible": True,
                "clickable": True,
            },
            {
                "key": "components",
                "title": "产品组件",
                "path": f"{self.namespace}/components",
                "visible": True,
                "clickable": True,
            },
            {
                "key": "reference",
                "title": "参考指南",
                "path": f"{self.namespace}/reference",
                "visible": True,
                "clickable": True,
            },
        ]

    def tab_config(self) -> list[dict[str, Any]]:
        configured = self.override.get("tabs")
        if not isinstance(configured, list):
            return self.default_tabs()
        result = []
        for index, item in enumerate(configured):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            path = _clean_repo_path(str(item.get("path", "")))
            key = str(item.get("key", f"custom-{index + 1}")).strip()
            result.append(
                {
                    "key": key or f"custom-{index + 1}",
                    "title": title,
                    "path": path,
                    "visible": _as_bool(item.get("visible"), True),
                    "clickable": _as_bool(item.get("clickable"), True)
                    and bool(path),
                }
            )
        return result

    def _tabs(
        self, active: str, *, include_hidden: bool = False
    ) -> list[NavigationTab]:
        tabs = []
        for definition in self.tab_config():
            path = definition["path"]
            visible = definition["visible"]
            if not visible and not include_hidden:
                continue
            if not path or storage.isdir(path) or storage.exists(path + ".md"):
                tabs.append(
                    NavigationTab(
                        key=definition["key"],
                        title=definition["title"],
                        path=self._landing_path(path) if path else "",
                        active=(
                            active == definition["key"]
                            or (
                                definition["key"]
                                not in ("manual", "components", "reference")
                                and bool(path)
                                and _is_page_active(self.pagepath, path)
                            )
                        ),
                        visible=visible,
                        clickable=definition["clickable"],
                    )
                )
        return tabs

    def _section_for_page(self) -> str:
        """按标签配置推导当前页面所属分类。

        标签路径可以在可视化编辑器里改成任意目录，因此不能只按
        ``{ns}/aps``、``{ns}/components``、``{ns}/reference`` 硬编码判断，
        否则自定义标签下的页面会落到「产品组件」并显示错误的树与高亮。
        """
        best = None
        for definition in self.tab_config():
            key = definition["key"]
            path = definition["path"]
            if key not in ("manual", "components", "reference") or not path:
                continue
            if not _is_page_active(self.pagepath, path):
                continue
            depth = len(split_path(path))
            if best is None or depth > best[0]:
                best = (depth, key)
        if best is not None:
            return best[1]
        if self._active_component_root() is not None:
            return "components"
        if _is_page_active(self.pagepath, f"{self.namespace}/aps"):
            return "manual"
        if _is_page_active(self.pagepath, f"{self.namespace}/reference"):
            return "reference"
        return "components"

    def build(self) -> NavigationTree:
        section = self._section_for_page()

        tree = self.base_tree(section)
        self._add_custom_entries(tree, section)
        self._apply_overrides(tree, section)
        tree.tabs = self._tabs(section)
        tree.title = (
            str(self.override.get("title", "文档目录")).strip() or "文档目录"
        )
        tree.section = section
        self._assign_numbers(tree)
        self._mark_active(tree)
        return tree

    def base_tree(self, section: str) -> NavigationTree:
        if section not in ("manual", "components", "reference"):
            section = "components"
        return copy.deepcopy(
            _cached_navigation_tree(
                str(storage.path),
                _repository_revision(),
                bool(app.config["RETAIN_PAGE_NAME_CASE"]),
                self.namespace,
                section,
            )
        )

    def section_config(self, section: str) -> dict[str, Any]:
        sections = self.override.get("sections", {})
        if not isinstance(sections, dict):
            return {}
        value = sections.get(section, {})
        return value if isinstance(value, dict) else {}

    def _find_entry(
        self, tree: OrderedDict[str, NavigationEntry], node_id: str
    ) -> NavigationEntry | None:
        for entry in tree.values():
            if entry.node_id == node_id:
                return entry
            found = self._find_entry(entry.children, node_id)
            if found is not None:
                return found
        return None

    def _add_custom_entries(self, tree: NavigationTree, section: str) -> None:
        custom = self.section_config(section).get("custom", [])
        if not isinstance(custom, list):
            return
        pending = [item for item in custom if isinstance(item, dict)]
        # 先建 id → entry 索引（单趟遍历），避免每个自定义项都全树查找；
        # 多趟循环只用于满足「父项可能排在子项之后」的序列化顺序。
        index_by_id: dict[str, NavigationEntry] = {}

        def reindex(tree_node) -> None:
            for entry in tree_node.values():
                index_by_id[entry.node_id] = entry
                reindex(entry.children)

        reindex(tree)
        for _ in range(len(pending) + 1):
            if not pending:
                break
            remaining = []
            for index, item in enumerate(pending):
                node_id = str(item.get("id", "")).strip()
                title = str(item.get("title", "")).strip()
                if not node_id or not title:
                    continue
                parent_id = str(item.get("parent", "")).strip()
                parent = index_by_id.get(parent_id) if parent_id else None
                if parent_id and parent is None:
                    remaining.append(item)
                    continue
                path = _clean_repo_path(str(item.get("path", "")))
                try:
                    order = float(item.get("order", index))
                except (TypeError, ValueError):
                    order = float(index)
                entry = NavigationEntry(
                    path=path or self.namespace,
                    header=title,
                    node_id=node_id,
                    scope=path,
                    linkable=_as_bool(item.get("clickable"), False)
                    and bool(path),
                    configured=True,
                    visible=_as_bool(item.get("visible"), True),
                    custom=True,
                    weight=order,
                )
                target = parent.children if parent is not None else tree
                self._insert(target, node_id, entry)
                index_by_id[node_id] = entry
            if len(remaining) == len(pending):
                # 父项不存在：明确记录，不再静默丢弃
                for item in remaining:
                    app.logger.warning(
                        "structured navigation: 自定义项 %r 的父项 %r 不存在，已忽略",
                        item.get("id"),
                        item.get("parent"),
                    )
                break
            pending = remaining

    def _apply_overrides(
        self,
        tree: OrderedDict[str, NavigationEntry],
        section: str,
        *,
        include_hidden: bool = False,
    ) -> None:
        section_config = self.section_config(section)
        overrides = section_config.get("nodes", {})
        if not isinstance(overrides, dict):
            overrides = {}

        ranked: list[tuple[float, int, str, NavigationEntry]] = []
        for index, (key, entry) in enumerate(tree.items()):
            override = overrides.get(entry.node_id, {})
            if not isinstance(override, dict):
                override = {}
            title = str(override.get("title", "")).strip()
            if title:
                entry.header = title
            entry.visible = _as_bool(override.get("visible"), entry.visible)
            entry.linkable = _as_bool(
                override.get("clickable"), entry.linkable
            ) and bool(entry.path)
            self._apply_overrides(
                entry.children,
                section,
                include_hidden=include_hidden,
            )
            try:
                default_order = entry.weight if entry.custom else index
                order = float(override.get("order", default_order))
            except (TypeError, ValueError):
                order = float(index)
            if entry.visible or include_hidden:
                ranked.append((order, index, key, entry))

        ranked.sort(key=lambda item: (item[0], item[1]))
        tree.clear()
        for _, _, key, entry in ranked:
            tree[key] = entry

    def editable_tree(self, section: str) -> NavigationTree:
        tree = self.base_tree(section)
        self._add_custom_entries(tree, section)
        self._apply_overrides(tree, section, include_hidden=True)
        tree.tabs = self._tabs(section, include_hidden=True)
        tree.title = (
            str(self.override.get("title", "文档目录")).strip() or "文档目录"
        )
        tree.section = section
        self._assign_numbers(tree)
        return tree

    @staticmethod
    def flatten_tree(
        tree: OrderedDict[str, NavigationEntry],
        parent: str = "",
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for order, entry in enumerate(tree.values()):
            rows.append(
                {
                    "id": entry.node_id,
                    "parent": parent,
                    "depth": depth,
                    "title": entry.header,
                    "path": (
                        entry.path
                        if entry.linkable or entry.custom
                        else entry.path
                    ),
                    "visible": entry.visible,
                    "clickable": entry.linkable,
                    "custom": entry.custom,
                    "has_children": bool(entry.children),
                    "order": order,
                }
            )
            rows.extend(
                StructuredNavigation.flatten_tree(
                    entry.children, entry.node_id, depth + 1
                )
            )
        return rows

    def _domains(self) -> list[dict[str, Any]]:
        return [item for item in self.model if isinstance(item, dict)]

    def _active_component_root(self) -> str | None:
        candidates = [
            root
            for root in self.mappings.values()
            if _is_page_active(self.pagepath, root)
            and _is_page_active(root, f"{self.namespace}/components")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda value: len(split_path(value)))

    def _build_component_tree(self, *, expand_all: bool) -> NavigationTree:
        tree = NavigationTree()
        for domain in self._domains():
            modules = domain.get("modules", [])
            if not isinstance(modules, list):
                continue
            for module in modules:
                if not isinstance(module, dict):
                    continue
                module_name = module.get("moduleName")
                components = module.get("components", [])
                if not isinstance(module_name, str) or not isinstance(
                    components, list
                ):
                    continue
                children: OrderedDict[str, NavigationEntry] = OrderedDict()
                for component in components:
                    if not isinstance(component, dict):
                        continue
                    code = component.get("code")
                    title = component.get("componentName")
                    if not isinstance(code, str) or not isinstance(title, str):
                        continue
                    root = self.mappings.get(code)
                    if root is None:
                        continue
                    entry = NavigationEntry(
                        path=self._landing_path(root),
                        header=title,
                        node_id=f"path:{root}",
                        scope=root,
                        configured=True,
                    )
                    if expand_all:
                        entry.children = self._build_directory(
                            root,
                            expand_all=True,
                        )
                    self._insert(children, code, entry)
                if not children:
                    continue
                module_entry = NavigationEntry(
                    path=self.namespace,
                    header=module_name,
                    node_id=f"module:{module.get('code', module_name)}",
                    children=children,
                    linkable=False,
                    configured=True,
                )
                self._insert(
                    tree, str(module.get("code", module_name)), module_entry
                )
        return tree

    def _build_section_tree(
        self, root: str, *, expand_all: bool
    ) -> NavigationTree:
        tree = NavigationTree()
        tree.update(self._build_directory(root, expand_all=expand_all))
        return tree

    def _mark_active(self, tree: OrderedDict[str, NavigationEntry]) -> bool:
        branch_active = False
        for entry in tree.values():
            child_active = self._mark_active(entry.children)
            own_active = bool(
                entry.scope and _is_page_active(self.pagepath, entry.scope)
            )
            entry.active = own_active or child_active
            branch_active = branch_active or entry.active
        return branch_active

    def _sidebar_config(self, directory: str) -> list[dict[str, Any]]:
        directory = _clean_repo_path(directory)
        if directory in self._sidebar_cache:
            return self._sidebar_cache[directory]
        for filename in SIDEBAR_FILENAMES:
            candidate = join_path([directory, filename])
            if not storage.exists(candidate):
                continue
            data = _safe_load_json(candidate)
            if isinstance(data, list):
                result = [item for item in data if isinstance(item, dict)]
                self._sidebar_cache[directory] = result
                return result
        self._sidebar_cache[directory] = []
        return self._sidebar_cache[directory]

    def _read_page(self, filename: str) -> tuple[str | None, float, bool]:
        if filename in self._page_cache:
            return self._page_cache[filename]
        try:
            content = storage.load(filename, size=8192)
        except StorageError:
            result = (None, 1000.0, False)
            self._page_cache[filename] = result
            return result
        metadata = get_frontmatter(content)
        title = get_header(content)
        weight_value = metadata.get("nav_weight", metadata.get("order", 1000))
        try:
            weight = float(weight_value)
        except (TypeError, ValueError):
            weight = 1000.0
        hidden = bool(metadata.get("hide", metadata.get("hidden", False)))
        result = (title, weight, hidden)
        self._page_cache[filename] = result
        return result

    def _list_directory(self, directory: str) -> tuple[list[str], list[str]]:
        directory = _clean_repo_path(directory)
        if directory not in self._directory_cache:
            self._directory_cache[directory] = storage.list(directory, depth=0)
        return self._directory_cache[directory]

    def _directory_landing_file(
        self,
        directory: str,
        visited: set[str] | None = None,
    ) -> str | None:
        """Resolve a directory rule to a real Markdown page.

        APStack sidebar paths frequently name a directory rather than the
        file inside it. Prefer conventional landing pages, then configured
        sidebar order, direct Markdown pages, and finally the first valid
        descendant page.
        """
        directory = _clean_repo_path(directory)
        if directory in self._landing_cache:
            return self._landing_cache[directory]
        visited = set() if visited is None else visited
        if directory in visited or not storage.isdir(directory):
            return None
        visited.add(directory)

        for filename in INDEX_FILENAMES:
            candidate = join_path([directory, filename])
            if storage.exists(candidate):
                self._landing_cache[directory] = candidate
                return candidate

        for filename in HTML_INDEX_FILENAMES:
            candidate = join_path([directory, filename])
            if storage.exists(candidate):
                self._landing_cache[directory] = candidate
                return candidate

        for spec in self._sidebar_config(directory):
            if bool(spec.get("hide", False)):
                continue
            path = spec.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            target = self._target_from_spec(directory, path)
            if storage.exists(target + ".md"):
                result = target + ".md"
                self._landing_cache[directory] = result
                return result
            landing = self._directory_landing_file(target, visited)
            if landing is not None:
                self._landing_cache[directory] = landing
                return landing

        files, directories = self._list_directory(directory)
        page_candidates: list[
            tuple[float, tuple[tuple[int, int | str], ...], str, str]
        ] = []
        for relative_file in files:
            filename = split_path(relative_file)[0]
            if not filename.lower().endswith(".md"):
                continue
            page_file = join_path([directory, filename])
            title, weight, hidden = self._read_page(page_file)
            if hidden:
                continue
            page_candidates.append(
                (
                    weight,
                    _natural_sort_key(filename),
                    (title or filename[:-3]).casefold(),
                    page_file,
                )
            )
        if page_candidates:
            page_candidates.sort()
            result = page_candidates[0][3]
            self._landing_cache[directory] = result
            return result

        directory_names = sorted(
            {split_path(path)[0] for path in directories if path},
            key=str.casefold,
        )
        for name in directory_names:
            landing = self._directory_landing_file(
                join_path([directory, name]), visited
            )
            if landing is not None:
                self._landing_cache[directory] = landing
                return landing
        self._landing_cache[directory] = None
        return None

    def _entry_for_target(
        self,
        target: str,
        *,
        title: str | None = None,
        configured: bool = False,
        config_base: str | None = None,
        child_specs: list[dict[str, Any]] | None = None,
        expand_all: bool = False,
        path_guard: frozenset[str] = frozenset(),
    ) -> NavigationEntry | None:
        target = _clean_repo_path(target)
        page_file = target + ".md"
        directory = target if storage.isdir(target) else None
        if storage.exists(page_file):
            page_title, weight, hidden = self._read_page(page_file)
            if hidden:
                return None
            entry = NavigationEntry(
                path=target,
                header=title or page_title or posixpath.basename(target),
                node_id=f"path:{target}",
                scope=target,
                configured=configured,
                weight=weight,
            )
            if directory and (
                expand_all or _is_page_active(self.pagepath, target)
            ):
                entry.children = self._build_directory(
                    directory,
                    expand_all=expand_all,
                    path_guard=path_guard,
                )
            return entry

        if directory is None:
            return None

        landing_file = self._directory_landing_file(directory)
        if landing_file is None:
            # Asset-only directories are not documentation nodes.
            return None
        page_title = None
        weight = 1000.0
        hidden = False
        if landing_file.lower().endswith(".md"):
            landing_title, landing_weight, landing_hidden = self._read_page(
                landing_file
            )
            if posixpath.basename(landing_file).lower() in INDEX_FILENAMES:
                page_title = landing_title
                weight = landing_weight
                hidden = landing_hidden
        if hidden:
            return None
        entry = NavigationEntry(
            path=self._landing_path(directory),
            header=title or page_title or posixpath.basename(directory),
            node_id=f"path:{directory}",
            scope=directory,
            configured=configured,
            linkable=True,
            weight=weight,
        )
        if expand_all or _is_page_active(self.pagepath, directory):
            if child_specs:
                base = config_base or directory
                entry.children = self._build_configured_children(
                    base, directory, child_specs, expand_all, path_guard
                )
            else:
                entry.children = self._build_directory(
                    directory,
                    expand_all=expand_all,
                    path_guard=path_guard,
                )
            if len(entry.children) == 1:
                only_child = next(iter(entry.children.values()))
                if not only_child.children and only_child.path == entry.path:
                    entry.children = OrderedDict()
        return entry

    def _target_from_spec(self, base: str, path: str) -> str:
        path = _clean_repo_path(path)
        return _clean_repo_path(join_path([base, path]))

    def _build_spec_entry(
        self,
        config_base: str,
        spec: dict[str, Any],
        expand_all: bool,
        path_guard: frozenset[str] = frozenset(),
    ) -> NavigationEntry | None:
        if bool(spec.get("hide", False)):
            return None
        path = spec.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        title = spec.get("title")
        if not isinstance(title, str) or not title.strip():
            title = None
        children = spec.get("children")
        child_specs = (
            [item for item in children if isinstance(item, dict)]
            if isinstance(children, list)
            else None
        )
        target = self._target_from_spec(config_base, path)
        return self._entry_for_target(
            target,
            title=title,
            configured=True,
            config_base=config_base,
            child_specs=child_specs,
            expand_all=expand_all,
            path_guard=path_guard,
        )

    def _immediate_candidates(
        self,
        directory: str,
        *,
        expand_all: bool,
        path_guard: frozenset[str] = frozenset(),
    ) -> OrderedDict[str, NavigationEntry]:
        files, directories = self._list_directory(directory)
        candidates: list[tuple[str, NavigationEntry]] = []
        directory_names = {split_path(path)[0] for path in directories if path}

        for name in sorted(directory_names, key=str.casefold):
            target = join_path([directory, name])
            entry = self._entry_for_target(
                target, expand_all=expand_all, path_guard=path_guard
            )
            if entry is not None:
                candidates.append((name, entry))

        for relative_file in files:
            filename = split_path(relative_file)[0]
            if not filename.lower().endswith(".md"):
                continue
            stem = filename[:-3]
            if stem.lower() in ("index", "readme") or stem in directory_names:
                continue
            target = join_path([directory, stem])
            entry = self._entry_for_target(
                target, expand_all=expand_all, path_guard=path_guard
            )
            if entry is not None:
                candidates.append((stem, entry))

        candidates.sort(
            key=lambda item: (
                item[1].weight,
                item[1].header.casefold(),
                item[0].casefold(),
            )
        )
        result: OrderedDict[str, NavigationEntry] = OrderedDict()
        for key, entry in candidates:
            self._insert(result, key, entry)
        return result

    def _build_configured_children(
        self,
        config_base: str,
        directory: str,
        specs: list[dict[str, Any]],
        expand_all: bool,
        path_guard: frozenset[str] = frozenset(),
    ) -> OrderedDict[str, NavigationEntry]:
        result: OrderedDict[str, NavigationEntry] = OrderedDict()
        used_targets: set[str] = set()
        for index, spec in enumerate(specs):
            spec_path = spec.get("path")
            target = None
            if isinstance(spec_path, str) and spec_path.strip():
                target = self._target_from_spec(config_base, spec_path)
                # A configured item owns its target even when hidden, so it
                # is not reintroduced by automatic discovery below.
                used_targets.add(target)
            if target is not None and target in path_guard:
                # 自引用或循环规则：保留节点本身，但不再展开子树
                app.logger.warning(
                    "structured navigation: 规则 %s 中的路径 %r 形成循环，"
                    "已跳过其子树",
                    config_base,
                    spec_path,
                )
                continue
            entry = self._build_spec_entry(
                config_base,
                spec,
                expand_all,
                path_guard | {target} if target is not None else path_guard,
            )
            if entry is None:
                continue
            self._insert(result, f"configured-{index}", entry)

        for key, entry in self._immediate_candidates(
            directory, expand_all=expand_all, path_guard=path_guard
        ).items():
            if any(
                _is_page_active(entry.path, used)
                or _is_page_active(used, entry.path)
                for used in used_targets
            ):
                continue
            self._insert(result, key, entry)
        return result

    def _build_directory(
        self,
        directory: str,
        *,
        expand_all: bool,
        path_guard: frozenset[str] = frozenset(),
    ) -> OrderedDict[str, NavigationEntry]:
        directory = _clean_repo_path(directory)
        if directory in path_guard:
            # 规则自引用（path: "."/".."）会回到自身目录，必须截断
            app.logger.warning(
                "structured navigation: 目录 %s 的规则形成自引用，已截断子树",
                directory,
            )
            return OrderedDict()
        path_guard = path_guard | {directory}
        specs = self._sidebar_config(directory)
        if specs:
            return self._build_configured_children(
                directory, directory, specs, expand_all, path_guard
            )
        return self._immediate_candidates(
            directory, expand_all=expand_all, path_guard=path_guard
        )

    @staticmethod
    def _insert(
        tree: OrderedDict[str, NavigationEntry],
        key: str,
        entry: NavigationEntry,
    ) -> None:
        key = key or entry.path or entry.header
        candidate = key
        index = 2
        while candidate in tree:
            candidate = f"{key}-{index}"
            index += 1
        tree[candidate] = entry

    def _assign_numbers(
        self,
        tree: OrderedDict[str, NavigationEntry],
        prefix: tuple[int, ...] = (),
    ) -> None:
        for index, entry in enumerate(tree.values(), start=1):
            numbers = prefix + (index,)
            entry.number = ".".join(str(value) for value in numbers)
            if entry.children:
                self._assign_numbers(entry.children, numbers)

    def _assign_unique_node_ids(
        self,
        tree: OrderedDict[str, NavigationEntry],
        parent_id: str,
    ) -> None:
        """Namespace path-based IDs by their parent to distinguish aliases.

        Imported sidebars can intentionally show the same landing page more
        than once under different headings.  A path alone is therefore not a
        safe editor key.  The parent digest keeps IDs compact while remaining
        stable when unrelated siblings are inserted or reordered.
        """
        parent_digest = hashlib.sha1(parent_id.encode("utf-8")).hexdigest()[
            :12
        ]
        occurrences: dict[str, int] = {}
        for entry in tree.values():
            if entry.node_id.startswith("module:") and parent_id.startswith(
                "section:"
            ):
                candidate = entry.node_id
            else:
                base = entry.node_id or f"path:{entry.scope or entry.path}"
                candidate = f"{base}:in:{parent_digest}"
            occurrences[candidate] = occurrences.get(candidate, 0) + 1
            if occurrences[candidate] > 1:
                candidate = f"{candidate}-{occurrences[candidate]}"
            entry.node_id = candidate
            self._assign_unique_node_ids(entry.children, candidate)


def _repository_revision() -> str:
    """缓存键：HEAD 提交 + Git 索引指纹。

    导航内容全部从**工作树**读取，而旧实现只用 HEAD 提交号做键：仓库处于
    未提交状态（外部拷入文档、pull 后工作树较新、storage.update 已落盘未
    提交）时同一 revision 下的内容变化不会改变键，导航会一直返回旧树。
    索引文件在工作树被 git add/commit/checkout 时必然变化，用它做指纹
    只需一次 stat（约 0.01ms），且能覆盖「有未提交改动」这一场景。
    """
    try:
        head = storage.repo.head.commit.hexsha
    except (TypeError, ValueError):
        head = "unborn"
    try:
        stat = os.stat(os.path.join(storage.path, ".git", "index"))
        fingerprint = f"{int(stat.st_mtime_ns)}:{stat.st_size}"
    except OSError:
        fingerprint = "no-index"
    return f"{head}:{fingerprint}"


@lru_cache(maxsize=24)
def _cached_navigation_tree(
    repository_path: str,
    revision: str,
    retain_page_name_case: bool,
    namespace: str,
    section: str,
) -> NavigationTree:
    """Build one immutable-by-convention full tree per repository revision."""
    del repository_path, revision, retain_page_name_case
    navigation = StructuredNavigation(namespace, namespace)
    if section == "manual":
        tree = navigation._build_section_tree(
            f"{namespace}/aps", expand_all=True
        )
    elif section == "reference":
        tree = navigation._build_section_tree(
            f"{namespace}/reference", expand_all=True
        )
    else:
        tree = navigation._build_component_tree(expand_all=True)

    navigation._assign_unique_node_ids(tree, f"section:{section}")

    tree.structured = True
    tree.title = "文档目录"
    tree.number_headings = True
    tree.namespace = namespace
    return tree


def clear_structured_navigation_cache() -> None:
    _cached_navigation_tree.cache_clear()


def structured_navigation_namespaces() -> list[str]:
    """Return repository roots that support the structured navigation UI."""
    _, directories = storage.list("", depth=0)
    namespaces = []
    for path in directories:
        namespace = _clean_repo_path(path)
        if namespace and (
            storage.exists(f"{namespace}/.portal.yml")
            and storage.exists(f"{namespace}/.idp/.model.yaml")
        ):
            namespaces.append(namespace)
    return sorted(set(namespaces), key=str.casefold)


def structured_navigation_cache_info():
    return _cached_navigation_tree.cache_info()


def build_structured_navigation(pagepath: str) -> NavigationTree | None:
    navigation = StructuredNavigation.for_page(pagepath)
    if navigation is None:
        return None
    try:
        return navigation.build()
    except RecursionError:
        # 规则互相引用时宁可回退到普通页面索引，也不能让整站页面 500
        app.logger.error(
            "structured navigation: %s 的规则存在循环引用，已回退到普通页面索引",
            pagepath,
        )
        clear_structured_navigation_cache()
        return None
    except Exception as error:  # pragma: no cover - 防御性兜底
        app.logger.exception(
            "structured navigation: %s 构建失败，已回退到普通页面索引：%s",
            pagepath,
            error,
        )
        return None


def number_document_headings(
    htmlcontent: str,
    toc: list[tuple[int, str, int, str, str]],
) -> tuple[str, list[tuple[int, str, int, str, str]]]:
    """Number rendered headings and TOC entries without changing anchors."""
    if not toc:
        return htmlcontent, toc

    counters = [0] * 6
    # 以文档首个标题的层级作为编号起点。若用全局最小层级，`###` 先于 `##`
    # 出现时首个标题会得到 normalized=1 而 counters[0] 仍为 0，编号显示成
    # "0.1"；跨级跳跃（## 后直接 ####）也会产生 "1.1.0.1" 这类编号。
    base_level = min(max(int(toc[0][2]), 1), 6)
    numbered_toc: list[tuple[int, str, int, str, str]] = []
    headings_by_anchor: dict[str, tuple[str, bool]] = {}

    for count, rendered, level, raw, anchor in toc:
        # 比首个标题更浅的标题回到顶层计数，避免出现 "0.1" 这样的编号
        depth = max(0, min(int(level), 6) - base_level)
        existing = NUMBER_PREFIX.match(raw.strip())
        if existing:
            parts = [
                int(value) for value in existing.group("number").split(".")
            ]
            for index, value in enumerate(parts[: len(counters)]):
                counters[index] = value
            for index in range(len(parts), len(counters)):
                counters[index] = 0
            number = existing.group("number")
            label = raw
        else:
            counters[depth] += 1
            for index in range(depth + 1, len(counters)):
                counters[index] = 0
            # 缺级时补齐，避免出现 "1.1.0.1" 这样的零段
            number = ".".join(
                str(value if value else 1) for value in counters[: depth + 1]
            )
            label = f"{number} {raw}"
        headings_by_anchor[anchor] = (number, existing is None)
        numbered_toc.append((count, rendered, level, label, anchor))

    def add_heading_number(match: re.Match[str]) -> str:
        id_match = HEADING_ID.search(match.group("open"))
        if id_match is None:
            return match.group(0)
        number_and_visibility = headings_by_anchor.get(
            html.unescape(id_match.group("anchor"))
        )
        if number_and_visibility is None or not number_and_visibility[1]:
            return match.group(0)
        number = html.escape(number_and_visibility[0])
        return (
            match.group("open")
            + f'<span class="heading-number">{number} </span>'
            + match.group("body")
            + match.group("close")
        )

    # Markdown headings are emitted in this controlled h1-h6 form.  A
    # targeted substitution avoids parsing and serializing large tables,
    # code blocks, and the rest of the document just to prefix a few titles.
    return HEADING_ELEMENT.sub(add_heading_number, htmlcontent), numbered_toc
