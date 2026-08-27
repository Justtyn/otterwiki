#!/usr/bin/env python3
"""Migrate the APStack MkDocs tree into an OtterWiki content repository.

The migration keeps the source directory untouched, places the content below
the ``apstack6`` wiki namespace, expands the source repository's ``include``
directives, and rewrites local links to their final OtterWiki URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

PAGE_UNSAFE = re.compile(r"[?.!#\\|$]")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INCLUDE = re.compile(r"^\s*include\((.+?)(?:\?level=(\d+))?\)\s*$")
HTML_LINK = re.compile(
    r"(?P<prefix>\b(?:src|href|poster)\s*=\s*)(?P<quote>['\"])(?P<url>.*?)(?P=quote)",
    re.IGNORECASE,
)
CSS_URL = re.compile(
    r"(?P<prefix>url\(\s*)(?P<quote>['\"]?)(?P<url>[^)'\"]+)(?P=quote)(?P<suffix>\s*\))",
    re.IGNORECASE,
)
ABSOLUTE_APSTACK_URL = re.compile(
    r"(?:\]\(|\b(?:src|href|poster)\s*=\s*['\"]|url\(\s*['\"]?)"
    r"(?P<url>/apstack6[^\s)'\"]*)",
    re.IGNORECASE,
)


@dataclass
class Stats:
    source_pages: int = 0
    source_assets: int = 0
    copied_assets: int = 0
    transformed_pages: int = 0
    transformed_html: int = 0
    transformed_css: int = 0
    expanded_includes: int = 0
    rewritten_links: int = 0
    unresolved_links: list[tuple[str, str]] = field(default_factory=list)


def page_component(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = PAGE_UNSAFE.sub("", value).lstrip("-").strip().lower()
    return value or "untitled"


def asset_component(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    # These characters cannot safely round-trip through an OtterWiki URL.
    return re.sub(r"[?#\\|$]", "-", value) or "attachment"


def map_relative(relative: PurePosixPath, *, page: bool) -> PurePosixPath:
    parts = list(relative.parts)
    if not parts:
        return PurePosixPath()
    mapped_dirs = [page_component(part) for part in parts[:-1]]
    if page:
        stem = page_component(PurePosixPath(parts[-1]).stem)
        return PurePosixPath(*mapped_dirs, stem + ".md")
    return PurePosixPath(*mapped_dirs, asset_component(parts[-1]))


class Migration:
    def __init__(
        self, source: Path, target_repo: Path, apply: bool, refresh: bool
    ):
        self.source = source.resolve()
        self.docs = (self.source / "docs").resolve()
        self.nav_docs = (self.source / "nav" / "docs").resolve()
        self.target_repo = target_repo.resolve()
        self.apply = apply
        self.refresh = refresh
        self.stats = Stats()
        self.file_map: dict[Path, PurePosixPath] = {}
        self.directory_map: dict[Path, PurePosixPath] = {}
        self.selected_files: list[Path] = []
        self.target_owners: dict[str, Path] = {}
        self.files_by_basename: dict[str, list[Path]] = {}

    @staticmethod
    def is_placeholder_page(path: Path) -> bool:
        """Match OtterWiki housekeeping's definition of an empty page."""
        if path.suffix.lower() != ".md" or path.stat().st_size > 512:
            return False
        content = path.read_text(
            encoding="utf-8-sig", errors="replace"
        ).strip()
        if not content:
            return True
        if re.fullmatch(r"# ([^ \r\n]+)", content):
            return True
        return content.count("\n") + 1 < 3

    def select(self) -> None:
        if not self.docs.is_dir():
            raise SystemExit(f"源文档目录不存在：{self.docs}")
        if not (self.target_repo / ".git").is_dir():
            raise SystemExit(
                f"目标不是 OtterWiki Git 内容库：{self.target_repo}"
            )

        self.selected_files.extend(
            path
            for path in self.docs.rglob("*")
            if path.is_file() and path.name != ".DS_Store"
        )

        for name in ("README.md", "APStack错误码总结.md"):
            path = self.source / name
            if path.is_file():
                self.selected_files.append(path)

        for relative in (".portal.yml", ".idp/.model.yaml"):
            path = self.source / relative
            if path.is_file():
                self.selected_files.append(path)

        for pattern in ("img*.png",):
            self.selected_files.extend(
                path for path in self.source.glob(pattern) if path.is_file()
            )
        root_img = self.source / "img"
        if root_img.is_dir():
            self.selected_files.extend(
                path
                for path in root_img.rglob("*")
                if path.is_file() and path.name != ".DS_Store"
            )

        if self.nav_docs.is_dir():
            self.selected_files.extend(
                path
                for path in self.nav_docs.rglob("*")
                if path.is_file() and path.name != ".DS_Store"
            )

        self.selected_files = sorted(set(self.selected_files))
        for source_file in self.selected_files:
            target = self.target_for(source_file)
            key = str(target).casefold()
            previous = self.target_owners.get(key)
            if previous is not None and previous != source_file:
                raise SystemExit(
                    "目标路径冲突："
                    f"{previous.relative_to(self.source)} 与 "
                    f"{source_file.relative_to(self.source)} -> {target}"
                )
            self.target_owners[key] = source_file
            self.file_map[source_file.resolve()] = target
            basename = unicodedata.normalize(
                "NFKC", source_file.name
            ).casefold()
            self.files_by_basename.setdefault(basename, []).append(
                source_file.resolve()
            )
            if source_file.suffix.lower() == ".md":
                self.stats.source_pages += 1
            else:
                self.stats.source_assets += 1

        directories = {path.parent.resolve() for path in self.selected_files}
        for directory in directories:
            self.directory_map[directory] = self.target_directory_for(
                directory
            )

    def target_for(self, source_file: Path) -> PurePosixPath:
        source_file = source_file.resolve()
        is_page = source_file.suffix.lower() == ".md"
        if source_file == self.docs / "index.md":
            return PurePosixPath("apstack6.md")
        if self.docs in source_file.parents:
            relative = PurePosixPath(
                source_file.relative_to(self.docs).as_posix()
            )
            return PurePosixPath("apstack6") / map_relative(
                relative, page=is_page
            )

        if source_file == self.source / "README.md":
            return PurePosixPath("apstack6/项目说明.md")
        if source_file == self.source / "APStack错误码总结.md":
            return PurePosixPath("apstack6/apstack错误码总结.md")
        if source_file == self.source / ".portal.yml":
            return PurePosixPath("apstack6/.portal.yml")
        if source_file == self.source / ".idp/.model.yaml":
            return PurePosixPath("apstack6/.idp/.model.yaml")
        if (
            source_file.parent == self.source
            or self.source / "img" in source_file.parents
        ):
            if source_file.parent == self.source:
                relative = PurePosixPath(source_file.name)
            else:
                relative = PurePosixPath(
                    source_file.relative_to(self.source).as_posix()
                )
            return PurePosixPath("apstack6/项目说明") / map_relative(
                relative, page=False
            )

        if source_file == self.nav_docs / "README.md":
            return PurePosixPath("apstack6/导航资料.md")
        if self.nav_docs in source_file.parents:
            relative = PurePosixPath(
                source_file.relative_to(self.nav_docs).as_posix()
            )
            return PurePosixPath("apstack6/导航资料") / map_relative(
                relative, page=is_page
            )
        raise ValueError(f"未定义目标映射：{source_file}")

    def target_directory_for(self, source_dir: Path) -> PurePosixPath:
        source_dir = source_dir.resolve()
        if source_dir == self.docs:
            return PurePosixPath("apstack6")
        if self.docs in source_dir.parents:
            relative = PurePosixPath(
                source_dir.relative_to(self.docs).as_posix()
            )
            return PurePosixPath("apstack6") / PurePosixPath(
                *(page_component(part) for part in relative.parts)
            )
        if source_dir == self.source:
            return PurePosixPath("apstack6/项目说明")
        if source_dir == self.source / ".idp":
            return PurePosixPath("apstack6/.idp")
        if (
            source_dir == self.source / "img"
            or self.source / "img" in source_dir.parents
        ):
            relative = PurePosixPath(
                source_dir.relative_to(self.source).as_posix()
            )
            return PurePosixPath("apstack6/项目说明") / PurePosixPath(
                *(page_component(part) for part in relative.parts)
            )
        if source_dir == self.nav_docs:
            return PurePosixPath("apstack6/导航资料")
        if self.nav_docs in source_dir.parents:
            relative = PurePosixPath(
                source_dir.relative_to(self.nav_docs).as_posix()
            )
            return PurePosixPath("apstack6/导航资料") / PurePosixPath(
                *(page_component(part) for part in relative.parts)
            )
        raise ValueError(f"未定义目录映射：{source_dir}")

    def resolve_local(self, source_page: Path, raw_url: str) -> str | None:
        raw_url = html.unescape(raw_url.strip())
        wrapped = raw_url.startswith("<") and raw_url.endswith(">")
        if wrapped:
            raw_url = raw_url[1:-1].strip()
        if not raw_url or raw_url.startswith(("#", "//")):
            return None

        parsed = urlsplit(raw_url)
        if parsed.scheme or parsed.netloc:
            return None
        source_path = unquote(parsed.path)
        if not source_path:
            return None

        candidates: list[Path] = []
        if source_path.startswith("/"):
            candidates.extend(
                [
                    self.docs / source_path.lstrip("/"),
                    self.source / source_path.lstrip("/"),
                ]
            )
        else:
            candidates.append(source_page.parent / source_path)
            # A few source links were written as if they were rooted at docs/.
            candidates.extend(
                [self.docs / source_path, self.source / source_path]
            )

        resolved: Path | None = None
        for candidate in candidates:
            candidate = Path(os.path.normpath(candidate)).resolve()
            variants = [candidate]
            if not candidate.suffix:
                variants.extend(
                    [
                        candidate.with_suffix(".md"),
                        candidate / "index.md",
                        candidate / "README.md",
                    ]
                )
            if candidate.is_dir():
                variants = [
                    candidate / "index.md",
                    candidate / "README.md",
                    candidate,
                ]
            for variant in variants:
                if (
                    variant.resolve() in self.file_map
                    or variant.resolve() in self.directory_map
                ):
                    resolved = variant.resolve()
                    break
            if resolved is not None:
                break

        if resolved is None:
            basename = unicodedata.normalize(
                "NFKC", Path(source_path).name
            ).casefold()
            basename_matches = self.files_by_basename.get(basename, [])
            # Repair a broken source-relative path only when the intended file
            # is unambiguous across the complete selected document set.
            if len(basename_matches) == 1:
                resolved = basename_matches[0]
            else:
                return None
        if resolved in self.file_map:
            target = self.file_map[resolved]
            if target.suffix.lower() == ".md":
                target = target.with_suffix("")
        else:
            target = self.directory_map[resolved]

        encoded_path = "/" + quote(target.as_posix(), safe="/@:-._~")
        return urlunsplit(
            ("", "", encoded_path, parsed.query, parsed.fragment)
        )

    def record_unresolved(self, source_page: Path, raw_url: str) -> None:
        raw_url = html.unescape(raw_url.strip("<> "))
        if not raw_url or raw_url.startswith(("#", "//", "/")):
            return
        parsed = urlsplit(raw_url)
        if parsed.scheme or parsed.netloc:
            return
        key = (source_page.relative_to(self.source).as_posix(), raw_url)
        if key not in self.stats.unresolved_links:
            self.stats.unresolved_links.append(key)

    def rewrite_url(self, source_page: Path, raw_url: str) -> str:
        rewritten = self.resolve_local(source_page, raw_url)
        if rewritten is None:
            self.record_unresolved(source_page, raw_url)
            return raw_url
        if rewritten != raw_url:
            self.stats.rewritten_links += 1
        return rewritten

    def split_destination(
        self, source_page: Path, inner: str
    ) -> tuple[str, str]:
        stripped = inner.strip()
        if stripped.startswith("<"):
            close = stripped.find(">")
            if close >= 0:
                return stripped[1:close], stripped[close + 1 :]

        # Prefer the whole value because the source contains unescaped spaces
        # in image filenames. Only split a trailing Markdown title if needed.
        if self.resolve_local(source_page, stripped) is not None:
            return stripped, ""
        title = re.match(
            r"^(.*?)(\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))$", stripped
        )
        if title:
            return title.group(1), title.group(2)
        return stripped, ""

    def rewrite_markdown_links(self, source_page: Path, line: str) -> str:
        output: list[str] = []
        cursor = 0
        while True:
            start = line.find("](", cursor)
            if start < 0:
                output.append(line[cursor:])
                break
            output.append(line[cursor : start + 2])
            depth = 1
            escaped = False
            pos = start + 2
            while pos < len(line):
                char = line[pos]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            if depth != 0:
                output.append(line[start + 2 :])
                break
            inner = line[start + 2 : pos]
            destination, suffix = self.split_destination(source_page, inner)
            rewritten = self.rewrite_url(source_page, destination)
            output.append(rewritten + suffix + ")")
            cursor = pos + 1
        return "".join(output)

    def rewrite_text_links(self, source_page: Path, text: str) -> str:
        def html_replacement(match: re.Match[str]) -> str:
            url = self.rewrite_url(source_page, match.group("url"))
            return f'{match.group("prefix")}{match.group("quote")}{url}{match.group("quote")}'

        return HTML_LINK.sub(html_replacement, text)

    def transform_markdown(
        self,
        source_page: Path,
        *,
        include_stack: tuple[Path, ...] = (),
        heading_level: int = 1,
        embedded: bool = False,
    ) -> str:
        text = source_page.read_text(encoding="utf-8-sig", errors="replace")
        if embedded and text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end >= 0:
                text = text[end + 5 :]

        result: list[str] = []
        fence_marker: str | None = None
        for line in text.splitlines(keepends=True):
            fence_match = FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence_marker is None:
                    fence_marker = marker[0]
                elif marker[0] == fence_marker:
                    fence_marker = None
                result.append(line)
                continue
            if fence_marker is not None:
                result.append(line)
                continue

            include_match = INCLUDE.match(line.rstrip("\r\n"))
            if include_match:
                include_ref = include_match.group(1)
                level = int(include_match.group(2) or "1")
                include_target = self.resolve_source_file(
                    source_page, include_ref
                )
                if (
                    include_target is not None
                    and include_target.suffix.lower() == ".md"
                    and include_target not in include_stack
                ):
                    expanded = self.transform_markdown(
                        include_target,
                        include_stack=include_stack + (source_page,),
                        heading_level=level,
                        embedded=True,
                    )
                    result.append(
                        f"\n<!-- 已从 {include_target.relative_to(self.source).as_posix()} 展开 -->\n\n"
                    )
                    result.append(expanded.rstrip() + "\n\n")
                    self.stats.expanded_includes += 1
                    continue

            if embedded and heading_level > 1:
                heading = re.match(r"^(\s{0,3})(#{1,6})(\s+.*)$", line)
                if heading:
                    new_level = min(
                        6, len(heading.group(2)) + heading_level - 1
                    )
                    line = (
                        heading.group(1) + "#" * new_level + heading.group(3)
                    )

            line = self.rewrite_markdown_links(source_page, line)
            line = self.rewrite_text_links(source_page, line)
            result.append(line)
        transformed = "".join(result)
        if not embedded and self.is_placeholder_page(source_page):
            if not transformed.strip():
                title = source_page.stem
                if title.casefold() in ("index", "readme"):
                    title = source_page.parent.name
                transformed = f"# {title}\n"
            transformed = transformed.rstrip() + (
                "\n\n> 此页面为文档目录入口，请使用左侧文档目录查看相关内容。\n"
            )
        return transformed

    def resolve_source_file(
        self, source_page: Path, raw_ref: str
    ) -> Path | None:
        path = unquote(urlsplit(raw_ref).path)
        candidate = Path(os.path.normpath(source_page.parent / path)).resolve()
        return candidate if candidate in self.file_map else None

    def transform_html(self, source_file: Path) -> str:
        text = source_file.read_text(encoding="utf-8-sig", errors="replace")
        return self.rewrite_text_links(source_file, text)

    def transform_css(self, source_file: Path) -> str:
        text = source_file.read_text(encoding="utf-8-sig", errors="replace")

        def replacement(match: re.Match[str]) -> str:
            url = self.rewrite_url(source_file, match.group("url"))
            return (
                match.group("prefix")
                + match.group("quote")
                + url
                + match.group("quote")
                + match.group("suffix")
            )

        return CSS_URL.sub(replacement, text)

    def write(self) -> None:
        namespace_page = self.target_repo / "apstack6.md"
        namespace_dir = self.target_repo / "apstack6"
        if (
            namespace_page.exists() or namespace_dir.exists()
        ) and not self.refresh:
            raise SystemExit(
                "目标已存在 apstack6.md 或 apstack6/；为避免覆盖现有内容，迁移已停止。"
            )

        if not self.apply:
            return

        for source_file in self.selected_files:
            relative_target = self.file_map[source_file.resolve()]
            destination = self.target_repo / relative_target
            destination.parent.mkdir(parents=True, exist_ok=True)
            suffix = source_file.suffix.lower()
            if suffix == ".md":
                destination.write_text(
                    self.transform_markdown(source_file),
                    encoding="utf-8",
                    newline="\n",
                )
                self.stats.transformed_pages += 1
            elif suffix in (".html", ".htm"):
                destination.write_text(
                    self.transform_html(source_file),
                    encoding="utf-8",
                    newline="\n",
                )
                self.stats.transformed_html += 1
            elif suffix == ".css":
                destination.write_text(
                    self.transform_css(source_file),
                    encoding="utf-8",
                    newline="\n",
                )
                self.stats.transformed_css += 1
            else:
                if not (
                    destination.exists()
                    and destination.stat().st_size
                    == source_file.stat().st_size
                    and destination.stat().st_mtime_ns
                    == source_file.stat().st_mtime_ns
                ):
                    shutil.copy2(source_file, destination)
                self.stats.copied_assets += 1

        self.write_home()
        self.write_report()

    def write_home(self) -> None:
        home = """---
title: APStack6 产品文档
---

# APStack6 产品文档

原 APStack6 文档库已迁移到当前 Wiki，正文、图片、SVG、HTML、配置示例和下载附件均保留。

- [进入 APStack6 产品文档](/apstack6)
- [查看原文档库说明](/apstack6/%E9%A1%B9%E7%9B%AE%E8%AF%B4%E6%98%8E)
- [查看迁移报告](/apstack6/%E8%BF%81%E7%A7%BB%E6%8A%A5%E5%91%8A)
"""
        (self.target_repo / "home.md").write_text(
            home, encoding="utf-8", newline="\n"
        )

    def write_report(self) -> None:
        by_suffix = Counter(
            path.suffix.lower() or "[无扩展名]" for path in self.selected_files
        )
        unresolved = self.stats.unresolved_links
        lines = [
            "---",
            "title: APStack6 文档迁移报告",
            "---",
            "",
            "# APStack6 文档迁移报告",
            "",
            "## 迁移范围",
            "",
            f"- Markdown 页面：{self.stats.source_pages}",
            f"- 图片及其他附件：{self.stats.source_assets}",
            f"- 已展开 include 指令：{self.stats.expanded_includes}",
            f"- 已重写本地链接：{self.stats.rewritten_links}",
            "- 结构规则：`.portal.yml`、`.idp/.model.yaml`、各级 `.sidebar.json(.bak)`",
            "- 空白占位页：已补充目录入口提示，避免被 Wiki 维护工具误删",
            "- 已排除：构建与发布脚本、隐藏工具缓存、`.DS_Store`",
            "",
            "## 文件类型",
            "",
            "| 类型 | 数量 |",
            "| --- | ---: |",
        ]
        lines.extend(
            f"| `{suffix}` | {count} |"
            for suffix, count in sorted(by_suffix.items())
        )
        lines.extend(["", "## 未解析的本地引用", ""])
        if unresolved:
            lines.append(
                "以下引用在源目录中也找不到对应文件，已原样保留，需后续人工确认："
            )
            lines.append("")
            for source_name, url in unresolved[:300]:
                lines.append(f"- `{source_name}` → `{url}`")
            if len(unresolved) > 300:
                lines.append(f"- 其余 {len(unresolved) - 300} 条未在本页展开")
        else:
            lines.append("未发现。")
        lines.append("")
        destination = self.target_repo / "apstack6" / "迁移报告.md"
        destination.write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )

    def summary(self) -> str:
        return "\n".join(
            [
                f"源页面：{self.stats.source_pages}",
                f"源附件：{self.stats.source_assets}",
                f"展开 include：{self.stats.expanded_includes}",
                f"重写链接：{self.stats.rewritten_links}",
                f"未解析引用：{len(self.stats.unresolved_links)}",
                f"模式：{'已写入' if self.apply else '仅检查'}",
            ]
        )

    @staticmethod
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def verify(self) -> str:
        missing: list[str] = []
        changed_assets: list[str] = []
        checked_assets = 0
        checked_urls = 0
        broken_urls: list[tuple[str, str]] = []

        for source_file, relative_target in self.file_map.items():
            destination = self.target_repo / relative_target
            if not destination.is_file():
                missing.append(relative_target.as_posix())
                continue
            if source_file.suffix.lower() not in (
                ".md",
                ".html",
                ".htm",
                ".css",
            ):
                checked_assets += 1
                if (
                    source_file.stat().st_size != destination.stat().st_size
                    or self.digest(source_file) != self.digest(destination)
                ):
                    changed_assets.append(relative_target.as_posix())

        text_targets = [
            self.target_repo / target
            for target in self.file_map.values()
            if target.suffix.lower() in (".md", ".html", ".htm", ".css")
        ]
        text_targets.append(self.target_repo / "apstack6" / "迁移报告.md")
        for target_file in text_targets:
            if not target_file.is_file():
                continue
            text = target_file.read_text(encoding="utf-8", errors="replace")
            for match in ABSOLUTE_APSTACK_URL.finditer(text):
                checked_urls += 1
                parsed = urlsplit(html.unescape(match.group("url")))
                path = self.target_repo / unquote(parsed.path).lstrip("/")
                if not (path.exists() or path.with_suffix(".md").is_file()):
                    broken_urls.append(
                        (
                            target_file.relative_to(
                                self.target_repo
                            ).as_posix(),
                            match.group("url"),
                        )
                    )

        if missing or changed_assets or broken_urls:
            details = [
                f"缺失目标文件：{len(missing)}",
                f"附件校验失败：{len(changed_assets)}",
                f"无效迁移链接：{len(broken_urls)}",
            ]
            details.extend(f"缺失：{item}" for item in missing[:20])
            details.extend(
                f"附件不一致：{item}" for item in changed_assets[:20]
            )
            details.extend(
                f"无效链接：{source} -> {url}"
                for source, url in broken_urls[:20]
            )
            raise SystemExit("\n".join(details))

        return "\n".join(
            [
                f"目标文件映射：{len(self.file_map)}/ {len(self.file_map)} 完整",
                f"附件 SHA-256：{checked_assets}/ {checked_assets} 一致",
                f"迁移后绝对链接：{checked_urls}/ {checked_urls} 可解析",
            ]
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target_repo", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="实际写入；默认只做映射和冲突检查"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="校验目标文件、附件哈希和迁移后链接",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="刷新已有 apstack6 命名空间中的迁移文件，不删除额外文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migration = Migration(
        args.source, args.target_repo, args.apply, args.refresh
    )
    migration.select()
    if args.verify:
        print(migration.verify())
        return 0
    migration.write()
    print(migration.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
