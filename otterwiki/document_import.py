#!/usr/bin/env python

"""Safe, administrator-triggered rebuilding of the APStack content repo."""

from __future__ import annotations

import os
import shutil
import stat
import importlib.util
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import git

try:
    from scripts.migrate_apstack_docs import Migration
except ModuleNotFoundError:
    # Existing source checkouts may have an editable environment created
    # before ``scripts`` became a package. Keep the new admin page usable
    # immediately after updating the checkout, without forcing a venv rebuild.
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "migrate_apstack_docs.py"
    )
    spec = importlib.util.spec_from_file_location(
        "otterwiki._apstack_migration", migration_path
    )
    if spec is None or spec.loader is None:
        raise
    migration_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = migration_module
    spec.loader.exec_module(migration_module)
    Migration = migration_module.Migration

CONFIRMATION_TEXT = "RESET APSTACK"
IMPORT_COMMIT_MESSAGE = "重新构建 APStack 文档"
IMPORT_AUTHOR = ("OtterWiki Importer", "noreply@otterwiki")
_document_import_lock = threading.Lock()


class DocumentImportError(Exception):
    """A validation or rebuild failure safe to show to an administrator."""


@dataclass
class ImportLimits:
    max_archive_size: int = 1024 * 1024 * 1024
    max_extracted_size: int = 2 * 1024 * 1024 * 1024
    max_files: int = 50_000


@dataclass
class DocumentImportResult:
    source_name: str
    commit: str
    pages: int
    assets: int
    rewritten_links: int
    unresolved_links: int
    verification: str
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _looks_like_apstack_root(path: Path) -> bool:
    return (
        (path / "docs").is_dir()
        and (path / ".portal.yml").is_file()
        and (path / ".idp" / ".model.yaml").is_file()
    )


def discover_apstack_root(base: Path) -> Path:
    """Find one APStack root, allowing a shallow ZIP wrapper directory."""
    base = base.resolve()
    if not base.is_dir():
        raise DocumentImportError("导入来源不是可读取的文件夹。")
    if _looks_like_apstack_root(base):
        return base

    candidates: list[Path] = []
    try:
        for current, directories, _ in os.walk(base, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(base).parts)
            except ValueError:
                continue
            directories[:] = [
                name
                for name in directories
                if name not in (".git", "__MACOSX")
            ]
            if depth >= 3:
                directories[:] = []
            if depth > 0 and _looks_like_apstack_root(current_path):
                candidates.append(current_path.resolve())
                directories[:] = []
    except OSError as error:
        raise DocumentImportError(f"无法扫描导入目录：{error}") from error

    candidates = sorted(set(candidates))
    if not candidates:
        raise DocumentImportError(
            "未找到 APStack 文档根目录；必须包含 docs/、.portal.yml "
            "和 .idp/.model.yaml。"
        )
    if len(candidates) > 1:
        names = "、".join(
            str(path.relative_to(base)) for path in candidates[:5]
        )
        raise DocumentImportError(
            f"来源中发现多个 APStack 文档根目录（{names}），请只保留一个。"
        )
    return candidates[0]


def save_upload(stream: BinaryIO, destination: Path, max_size: int) -> int:
    """Persist an upload while enforcing a hard byte limit."""
    written = 0
    try:
        with destination.open("xb") as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size:
                    raise DocumentImportError(
                        f"ZIP 文件超过允许上限（{max_size // (1024 * 1024)} MiB）。"
                    )
                output.write(chunk)
    except DocumentImportError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise DocumentImportError(f"保存上传文件失败：{error}") from error
    return written


def _safe_zip_target(root: Path, filename: str) -> tuple[Path, str]:
    if "\x00" in filename:
        raise DocumentImportError("ZIP 中包含无效的空字符路径。")
    normalized = filename.replace("\\", "/")
    member = PurePosixPath(normalized)
    if (
        member.is_absolute()
        or any(part in ("", ".", "..") for part in member.parts)
        or (member.parts and ":" in member.parts[0])
    ):
        raise DocumentImportError(f"ZIP 中包含不安全路径：{filename}")
    target = root.joinpath(*member.parts)
    resolved_parent = target.parent.resolve()
    if not _is_relative_to(resolved_parent, root.resolve()):
        raise DocumentImportError(f"ZIP 路径越界：{filename}")
    return target, normalized.rstrip("/").casefold()


def extract_zip_safely(
    archive_path: Path,
    destination: Path,
    limits: ImportLimits,
) -> None:
    """Extract a ZIP without traversal, links, devices, or zip bombs."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise DocumentImportError("上传文件不是有效的 ZIP 压缩包。") from error

    with archive:
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        if len(infos) > limits.max_files:
            raise DocumentImportError(
                f"ZIP 条目数量超过上限（{limits.max_files} 个）。"
            )
        declared_size = sum(info.file_size for info in files)
        if declared_size > limits.max_extracted_size:
            raise DocumentImportError(
                "ZIP 解压后大小超过允许上限"
                f"（{limits.max_extracted_size // (1024 * 1024)} MiB）。"
            )

        seen: set[str] = set()
        validated: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in infos:
            if info.flag_bits & 0x1:
                raise DocumentImportError("不支持加密 ZIP 文件。")
            target, key = _safe_zip_target(destination, info.filename)
            if key in seen:
                raise DocumentImportError(
                    f"ZIP 中包含重复或大小写冲突路径：{info.filename}"
                )
            seen.add(key)

            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR):
                raise DocumentImportError(
                    f"ZIP 中包含符号链接或特殊文件：{info.filename}"
                )
            validated.append((info, target))

        extracted_size = 0
        try:
            for info, target in validated:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        extracted_size += len(chunk)
                        if extracted_size > limits.max_extracted_size:
                            raise DocumentImportError(
                                "ZIP 实际解压大小超过允许上限。"
                            )
                        output.write(chunk)
        except DocumentImportError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise DocumentImportError(f"ZIP 解压失败：{error}") from error


def _validate_selected_files(
    migration: Migration,
    limits: ImportLimits,
) -> None:
    selected = migration.selected_files
    if len(selected) > limits.max_files:
        raise DocumentImportError(
            f"文档文件数量超过上限（{limits.max_files} 个）。"
        )
    total_size = 0
    for path in selected:
        if path.is_symlink():
            raise DocumentImportError(f"来源包含符号链接，不允许导入：{path}")
        try:
            total_size += path.stat().st_size
        except OSError as error:
            raise DocumentImportError(
                f"无法读取来源文件 {path}：{error}"
            ) from error
        if total_size > limits.max_extracted_size:
            raise DocumentImportError("文档目录总大小超过允许上限。")


def _run_migration(
    source_root: Path,
    stage_repo: Path,
    limits: ImportLimits,
    author: tuple[str, str],
) -> tuple[Migration, str, str]:
    stage_repo.mkdir(parents=True)
    repo = git.Repo.init(stage_repo)
    try:
        with repo.config_writer() as config:
            config.set_value("receive", "denyCurrentBranch", "updateInstead")
        migration = Migration(source_root, stage_repo, True, False)
        try:
            migration.select()
            _validate_selected_files(migration, limits)
            migration.write()
            verification = migration.verify()
        except SystemExit as error:
            message = str(error) or "APStack 文档解析失败。"
            raise DocumentImportError(message) from error
        except DocumentImportError:
            raise
        except (OSError, ValueError, git.GitError) as error:
            raise DocumentImportError(
                f"构建新文档仓库失败：{error}"
            ) from error

        try:
            repo.git.add(all=True)
            commit = repo.index.commit(
                IMPORT_COMMIT_MESSAGE,
                author=git.Actor(author[0], author[1]),
            )
        except (OSError, ValueError, git.GitError) as error:
            raise DocumentImportError(
                f"创建新 Git 仓库提交失败：{error}"
            ) from error
        return migration, verification, commit.hexsha
    finally:
        # GitPython keeps memory-mapped files alive on Windows until close().
        # Leaving this repository open prevents the staged directory from
        # being moved or removed and surfaces as WinError 32.
        repo.close()


def _remove_tree_with_retries(path: Path) -> None:
    """Remove a tree despite Windows read-only attributes and short locks."""

    def make_writable_and_retry(function, filename, _exc_info):
        mode = os.stat(filename, follow_symlinks=False).st_mode
        os.chmod(filename, mode | stat.S_IWRITE)
        function(filename)

    last_error: OSError | None = None
    for delay in (0, 0.1, 0.25, 0.5, 1.0):
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(path, onerror=make_writable_and_retry)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
    if last_error is not None:
        raise last_error


def _cleanup_repository_backups(target: Path, backup: Path) -> list[str]:
    """Remove the current and any stale pre-import backups."""
    warnings: list[str] = []
    candidates = {backup}
    candidates.update(target.parent.glob(f".{target.name}.pre-import-*"))
    for candidate in sorted(candidates, key=str):
        if candidate.is_symlink():
            warnings.append(f"拒绝删除符号链接形式的旧仓库备份：{candidate}")
            continue
        try:
            _remove_tree_with_retries(candidate)
        except OSError as error:
            warnings.append(f"旧仓库备份未能自动删除：{candidate}（{error}）")
    return warnings


def _replace_repository(storage, stage_repo: Path) -> list[str]:
    target = Path(storage.path).resolve()
    backup = target.parent / f".{target.name}.pre-import-{uuid.uuid4().hex}"
    failed_new = (
        target.parent / f".{target.name}.failed-import-{uuid.uuid4().hex}"
    )
    warnings: list[str] = []

    if target.is_symlink() or not target.is_dir():
        raise DocumentImportError("当前内容仓库路径不是普通文件夹，拒绝替换。")
    try:
        # Windows refuses to rename a Git working tree while GitPython still
        # holds memory maps or subprocess handles below .git.
        storage.repo.close()
    except Exception as error:
        raise DocumentImportError(
            f"无法释放当前 Git 仓库文件句柄：{error}"
        ) from error
    try:
        os.replace(target, backup)
        try:
            os.replace(stage_repo, target)
        except OSError:
            os.replace(backup, target)
            storage.repo = storage._read_repo()
            raise

        try:
            storage.repo = storage._read_repo()
        except Exception:
            os.replace(target, failed_new)
            os.replace(backup, target)
            storage.repo = storage._read_repo()
            shutil.rmtree(failed_new, ignore_errors=True)
            raise
    except OSError as error:
        if target.is_dir():
            try:
                storage.repo = storage._read_repo()
            except Exception:
                pass
        raise DocumentImportError(
            f"替换内容仓库失败，旧仓库已保留：{error}"
        ) from error
    except Exception as error:
        raise DocumentImportError(
            f"加载新内容仓库失败，已恢复旧仓库：{error}"
        ) from error

    warnings.extend(_cleanup_repository_backups(target, backup))
    return warnings


def reset_repository_from_source(
    source: Path,
    storage,
    *,
    limits: ImportLimits | None = None,
    author: tuple[str, str] = IMPORT_AUTHOR,
) -> DocumentImportResult:
    """Build a fresh Git repo from APStack source, then replace the live repo."""
    started = time.monotonic()
    limits = limits or ImportLimits()
    source_root = discover_apstack_root(source)
    for required in (
        source_root / "docs",
        source_root / ".portal.yml",
        source_root / ".idp" / ".model.yaml",
    ):
        if required.is_symlink():
            raise DocumentImportError(
                f"APStack 必需路径不能是符号链接：{required}"
            )
    target = Path(storage.path).resolve()
    if _is_relative_to(source_root, target) or _is_relative_to(
        target, source_root
    ):
        raise DocumentImportError(
            "导入来源不能位于当前内容仓库内部或包含该仓库。"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".otterwiki-apstack-build-", dir=target.parent
        ) as workspace_name:
            stage_repo = Path(workspace_name) / "repository"
            migration, verification, commit = _run_migration(
                source_root, stage_repo, limits, author
            )
            from otterwiki.repomgmt import RepositoryManager

            with RepositoryManager.git_push_pull_Lock:
                warnings = _replace_repository(storage, stage_repo)
    except DocumentImportError:
        raise
    except OSError as error:
        raise DocumentImportError(f"临时构建目录处理失败：{error}") from error

    from otterwiki.structured_navigation import (
        clear_structured_navigation_cache,
    )

    clear_structured_navigation_cache()
    storage.notify_repository_changed_from_external()
    return DocumentImportResult(
        source_name=source_root.name,
        commit=commit,
        pages=migration.stats.source_pages,
        assets=migration.stats.source_assets,
        rewritten_links=migration.stats.rewritten_links,
        unresolved_links=len(migration.stats.unresolved_links),
        verification=verification,
        duration_seconds=time.monotonic() - started,
        warnings=warnings,
    )


def _configured_limits(app) -> ImportLimits:
    try:
        return ImportLimits(
            max_archive_size=int(
                app.config["APSTACK_IMPORT_MAX_ARCHIVE_SIZE"]
            ),
            max_extracted_size=int(
                app.config["APSTACK_IMPORT_MAX_EXTRACTED_SIZE"]
            ),
            max_files=int(app.config["APSTACK_IMPORT_MAX_FILES"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DocumentImportError("APStack 导入大小限制配置无效。") from error


def document_import_form(
    *,
    result: DocumentImportResult | None = None,
    error: str | None = None,
    source_directory: str = "",
    status: int = 200,
):
    from flask import abort, render_template, session

    from otterwiki.auth import has_permission
    from otterwiki.server import app

    if not has_permission("ADMIN"):
        abort(403)
    if result is None and error is None:
        stored_result = session.pop("document_import_result", None)
        if isinstance(stored_result, dict):
            try:
                result = DocumentImportResult(**stored_result)
            except TypeError:
                result = None
    limits = _configured_limits(app)
    return (
        render_template(
            "admin/document_import.html",
            title="重置并导入文档",
            confirmation_text=CONFIRMATION_TEXT,
            limits=limits,
            result=result,
            import_error=error,
            source_directory=source_directory,
        ),
        status,
    )


def handle_document_import(form, files):
    from dataclasses import asdict

    from flask import abort, redirect, session, url_for

    from otterwiki.auth import get_author, has_permission
    from otterwiki.helper import toast
    from otterwiki.models import Drafts
    from otterwiki.server import app, db, storage

    if not has_permission("ADMIN"):
        abort(403)
    source_directory = form.get("source_directory", "").strip()
    upload = files.get("archive")
    has_upload = bool(upload and upload.filename)

    if form.get("confirmation", "").strip() != CONFIRMATION_TEXT:
        return document_import_form(
            error=f"请输入 {CONFIRMATION_TEXT} 以确认重置。",
            source_directory=source_directory,
            status=400,
        )
    if has_upload == bool(source_directory):
        return document_import_form(
            error="必须且只能选择一种来源：上传 ZIP 或服务器文件夹。",
            source_directory=source_directory,
            status=400,
        )
    limits = _configured_limits(app)
    if not _document_import_lock.acquire(blocking=False):
        return document_import_form(
            error="已有文档导入任务正在执行，请稍后再试。",
            source_directory=source_directory,
            status=409,
        )

    try:
        if has_upload:
            if not upload.filename.lower().endswith(".zip"):
                raise DocumentImportError("仅支持上传 .zip 压缩包。")
            repository_parent = Path(storage.path).resolve().parent
            with tempfile.TemporaryDirectory(
                prefix=".otterwiki-apstack-upload-", dir=repository_parent
            ) as workspace_name:
                workspace = Path(workspace_name)
                archive_path = workspace / "upload.zip"
                extracted = workspace / "extracted"
                extracted.mkdir()
                save_upload(
                    upload.stream, archive_path, limits.max_archive_size
                )
                extract_zip_safely(archive_path, extracted, limits)
                result = reset_repository_from_source(
                    extracted,
                    storage,
                    limits=limits,
                    author=get_author(),
                )
                result.source_name = Path(
                    upload.filename.replace("\\", "/")
                ).name
        else:
            source_path = Path(source_directory).expanduser()
            if not source_path.is_absolute():
                raise DocumentImportError("服务器文件夹必须填写绝对路径。")
            result = reset_repository_from_source(
                source_path,
                storage,
                limits=limits,
                author=get_author(),
            )

    except DocumentImportError as error:
        db.session.rollback()
        app.logger.warning("APStack document import rejected: %s", error)
        return document_import_form(
            error=str(error),
            source_directory=source_directory,
            status=400,
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Unexpected APStack document import failure")
        return document_import_form(
            error="导入过程中发生内部错误，旧仓库已保留；请查看服务器日志。",
            source_directory=source_directory,
            status=500,
        )
    finally:
        _document_import_lock.release()

    try:
        Drafts.query.delete()
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        app.logger.exception("Unable to clear drafts after APStack import")
        result.warnings.append(f"旧草稿未能自动清理：{error}")

    toast(
        f"APStack 文档已重建：{result.pages} 个页面，"
        f"{result.assets} 个附件。"
    )
    session["document_import_result"] = asdict(result)
    return redirect(url_for("admin_document_import"))
