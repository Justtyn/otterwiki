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
import html
import json
import posixpath
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from otterwiki.gitstorage import StorageError
from otterwiki.server import app, storage
from otterwiki.util import (
    get_frontmatter,
    get_header,
    join_path,
    sanitize_pagename,
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
    title: str
    path: str
    active: bool = False


@dataclass
class NavigationEntry:
    path: str
    header: str
    scope: str = ""
    children: OrderedDict[str, "NavigationEntry"] = field(
        default_factory=OrderedDict
    )
    number: str = ""
    linkable: bool = True
    active: bool = False
    configured: bool = False
    weight: float = 1000.0


class NavigationTree(OrderedDict[str, NavigationEntry]):
    """Ordered tree with presentation metadata consumed by Jinja."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.structured = False
        self.title = "页面索引"
        self.tabs: list[NavigationTab] = []
        self.number_headings = False
        self.namespace = ""


def _clean_repo_path(value: str) -> str:
    value = value.replace("\\", "/").strip("/")
    value = sanitize_pagename(value, handle_md=True)
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
        self.portal = _safe_load_yaml(self.portal_path)
        self.model = _safe_load_yaml(self.model_path)
        self.mappings = self._load_mappings()

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
                return (
                    landing_file[:-3]
                    if landing_file.lower().endswith(".md")
                    else landing_file
                )
        if storage.exists(path + ".md"):
            return path
        return path

    def _tabs(self, active: str) -> list[NavigationTab]:
        definitions = (
            ("产品手册", f"{self.namespace}/aps", "manual"),
            ("产品组件", f"{self.namespace}/components", "components"),
            ("参考指南", f"{self.namespace}/reference", "reference"),
        )
        tabs = []
        for title, path, key in definitions:
            if storage.isdir(path) or storage.exists(path + ".md"):
                tabs.append(
                    NavigationTab(
                        title=title,
                        path=self._landing_path(path),
                        active=active == key,
                    )
                )
        return tabs

    def build(self) -> NavigationTree:
        if self._active_component_root() is not None:
            section = "components"
        elif _is_page_active(self.pagepath, f"{self.namespace}/aps"):
            section = "manual"
        elif _is_page_active(self.pagepath, f"{self.namespace}/reference"):
            section = "reference"
        else:
            section = "components"

        tree = copy.deepcopy(
            _cached_navigation_tree(
                str(storage.path),
                _repository_revision(),
                bool(app.config["RETAIN_PAGE_NAME_CASE"]),
                self.namespace,
                section,
            )
        )
        tree.tabs = self._tabs(section)
        self._mark_active(tree)
        return tree

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
                scope=target,
                configured=configured,
                weight=weight,
            )
            if directory and (
                expand_all or _is_page_active(self.pagepath, target)
            ):
                entry.children = self._build_directory(
                    directory, expand_all=expand_all
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
            scope=directory,
            configured=configured,
            linkable=True,
            weight=weight,
        )
        if expand_all or _is_page_active(self.pagepath, directory):
            if child_specs:
                base = config_base or directory
                entry.children = self._build_configured_children(
                    base, directory, child_specs, expand_all
                )
            else:
                entry.children = self._build_directory(
                    directory, expand_all=expand_all
                )
            if len(entry.children) == 1:
                only_child = next(iter(entry.children.values()))
                if not only_child.children and only_child.path == entry.path:
                    entry.children = OrderedDict()
        return entry

    def _target_from_spec(self, base: str, path: str) -> str:
        path = _clean_repo_path(path)
        return join_path([base, path])

    def _build_spec_entry(
        self,
        config_base: str,
        spec: dict[str, Any],
        expand_all: bool,
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
        )

    def _immediate_candidates(
        self, directory: str, *, expand_all: bool
    ) -> OrderedDict[str, NavigationEntry]:
        files, directories = self._list_directory(directory)
        candidates: list[tuple[str, NavigationEntry]] = []
        directory_names = {split_path(path)[0] for path in directories if path}

        for name in sorted(directory_names, key=str.casefold):
            target = join_path([directory, name])
            entry = self._entry_for_target(target, expand_all=expand_all)
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
            entry = self._entry_for_target(target, expand_all=expand_all)
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
    ) -> OrderedDict[str, NavigationEntry]:
        result: OrderedDict[str, NavigationEntry] = OrderedDict()
        used_targets: set[str] = set()
        for index, spec in enumerate(specs):
            spec_path = spec.get("path")
            if isinstance(spec_path, str) and spec_path.strip():
                # A configured item owns its target even when hidden, so it
                # is not reintroduced by automatic discovery below.
                used_targets.add(
                    self._target_from_spec(config_base, spec_path)
                )
            entry = self._build_spec_entry(config_base, spec, expand_all)
            if entry is None:
                continue
            self._insert(result, f"configured-{index}", entry)

        for key, entry in self._immediate_candidates(
            directory, expand_all=expand_all
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
        self, directory: str, *, expand_all: bool
    ) -> OrderedDict[str, NavigationEntry]:
        specs = self._sidebar_config(directory)
        if specs:
            return self._build_configured_children(
                directory, directory, specs, expand_all
            )
        return self._immediate_candidates(directory, expand_all=expand_all)

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


def _repository_revision() -> str:
    try:
        return storage.repo.head.commit.hexsha
    except (TypeError, ValueError):
        return "unborn"


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

    tree.structured = True
    tree.title = "文档目录"
    tree.number_headings = True
    tree.namespace = namespace
    navigation._assign_numbers(tree)
    return tree


def clear_structured_navigation_cache() -> None:
    _cached_navigation_tree.cache_clear()


def structured_navigation_cache_info():
    return _cached_navigation_tree.cache_info()


def build_structured_navigation(pagepath: str) -> NavigationTree | None:
    navigation = StructuredNavigation.for_page(pagepath)
    return navigation.build() if navigation is not None else None


def number_document_headings(
    htmlcontent: str,
    toc: list[tuple[int, str, int, str, str]],
) -> tuple[str, list[tuple[int, str, int, str, str]]]:
    """Number rendered headings and TOC entries without changing anchors."""
    if not toc:
        return htmlcontent, toc

    minimum_level = min(item[2] for item in toc)
    counters = [0] * 6
    numbered_toc: list[tuple[int, str, int, str, str]] = []
    headings_by_anchor: dict[str, tuple[str, bool]] = {}

    for count, rendered, level, raw, anchor in toc:
        normalized = max(0, level - minimum_level)
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
            counters[normalized] += 1
            for index in range(normalized + 1, len(counters)):
                counters[index] = 0
            number = ".".join(
                str(value) for value in counters[: normalized + 1]
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
