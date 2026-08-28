#!/usr/bin/env python

import io
import stat
import zipfile
from pathlib import Path

import pytest

from otterwiki.document_import import (
    CONFIRMATION_TEXT,
    DocumentImportError,
    ImportLimits,
    _make_tree_writable,
    _remove_tree_with_retries,
    _replace_repository,
    _run_migration,
    extract_zip_safely,
    reset_repository_from_source,
)


def _write_apstack_source(root: Path) -> Path:
    (root / "docs/components/demo").mkdir(parents=True)
    (root / ".idp").mkdir()
    (root / "docs/index.md").write_text(
        "# APStack 产品文档\n\n[示例组件](components/demo/index.md)\n",
        encoding="utf-8",
    )
    (root / "docs/components/index.md").write_text(
        "# 产品组件\n", encoding="utf-8"
    )
    (root / "docs/components/demo/index.md").write_text(
        "# 示例组件\n", encoding="utf-8"
    )
    (root / "docs/components/demo/logo.png").write_bytes(b"fake-png")
    (root / ".portal.yml").write_text(
        """mappings:
  - doc_code: demo-component
    file_path: docs/components/demo
""",
        encoding="utf-8",
    )
    (root / ".idp/.model.yaml").write_text(
        """- domainName: Demo
  code: demo
  modules:
    - moduleName: 开发平台
      code: development
      components:
        - componentName: 示例组件
          code: demo-component
""",
        encoding="utf-8",
    )
    return root


def _zip_source(source: Path) -> io.BytesIO:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(
                    path, Path("APStackDoc") / path.relative_to(source)
                )
    output.seek(0)
    return output


def test_reset_repository_rebuilds_git_and_imports_apstack(
    create_app, tmp_path
):
    source = _write_apstack_source(tmp_path / "APStackDoc")
    storage = create_app.storage
    old_commit = storage.repo.head.commit.hexsha
    storage.store(
        "old-page.md",
        "# 即将删除\n",
        author=("Test", "test@example.org"),
        message="old history",
    )
    target = Path(storage.path)
    stale_backup = target.parent / f".{target.name}.pre-import-stale"
    stale_backup.mkdir()
    stale_file = stale_backup / "old-object"
    stale_file.write_bytes(b"old git object")
    stale_file.chmod(stat.S_IREAD)

    result = reset_repository_from_source(source, storage)

    assert result.pages == 3
    assert result.assets == 3  # logo and two navigation metadata files
    assert result.commit != old_commit
    assert storage.exists("apstack6.md")
    assert storage.exists("apstack6/components/demo/index.md")
    assert storage.exists("apstack6/components/demo/logo.png")
    assert not storage.exists("old-page.md")
    assert not stale_backup.exists()
    assert not list(target.parent.glob(f".{target.name}.pre-import-*"))
    assert len(storage.log()) == 1
    assert storage.repo.head.commit.hexsha == result.commit
    assert (
        storage.repo.config_reader().get_value("receive", "denyCurrentBranch")
        == "updateInstead"
    )


def test_staging_repository_is_closed_before_it_is_moved(tmp_path):
    source = _write_apstack_source(tmp_path / "APStackDoc")
    stage = tmp_path / "stage"

    _run_migration(
        source,
        stage,
        ImportLimits(),
        ("Test", "test@example.org"),
    )

    # Open GitPython memory maps prevent this rename on Windows (WinError 32).
    moved = tmp_path / "moved-stage"
    stage.rename(moved)
    assert (moved / ".git").is_dir()


def test_tree_removal_retries_after_windows_access_denied(
    tmp_path, monkeypatch
):
    import otterwiki.document_import as document_import

    tree = tmp_path / "old-repository"
    tree.mkdir()
    (tree / "object").write_bytes(b"git object")
    real_rmtree = document_import.shutil.rmtree
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "simulated Windows access denied")
        return real_rmtree(*args, **kwargs)

    monkeypatch.setattr(document_import.shutil, "rmtree", fail_once)
    monkeypatch.setattr(document_import.time, "sleep", lambda _delay: None)

    _remove_tree_with_retries(tree)

    assert attempts == 2
    assert not tree.exists()


def test_make_tree_writable_clears_readonly_bits(tmp_path):
    tree = tmp_path / "old-repository"
    objects = tree / "objects" / "07"
    objects.mkdir(parents=True)
    obj = objects / "object"
    obj.write_bytes(b"git object")
    obj.chmod(stat.S_IREAD)
    objects.chmod(stat.S_IREAD | stat.S_IEXEC)

    _make_tree_writable(tree)

    assert obj.stat().st_mode & stat.S_IWRITE
    assert objects.stat().st_mode & stat.S_IWRITE


def test_invalid_source_does_not_modify_existing_repository(
    create_app, tmp_path
):
    source = tmp_path / "invalid"
    source.mkdir()
    storage = create_app.storage
    old_commit = storage.repo.head.commit.hexsha
    old_home = storage.load("home.md")

    with pytest.raises(DocumentImportError, match="未找到 APStack"):
        reset_repository_from_source(source, storage)

    assert storage.repo.head.commit.hexsha == old_commit
    assert storage.load("home.md") == old_home


def test_repository_reload_failure_restores_old_repository(
    create_app, tmp_path, monkeypatch
):
    import git

    storage = create_app.storage
    old_commit = storage.repo.head.commit.hexsha
    old_home = storage.load("home.md")
    stage = tmp_path / "stage"
    repo = git.Repo.init(stage)
    (stage / "replacement.md").write_text("# Replacement\n", encoding="utf-8")
    repo.git.add(all=True)
    repo.index.commit(
        "replacement", author=git.Actor("Test", "test@example.org")
    )
    repo.close()

    original_read_repo = storage._read_repo
    calls = 0

    def fail_new_repository_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated reload failure")
        return original_read_repo()

    monkeypatch.setattr(storage, "_read_repo", fail_new_repository_once)

    with pytest.raises(DocumentImportError, match="已恢复旧仓库"):
        _replace_repository(storage, stage)

    assert storage.repo.head.commit.hexsha == old_commit
    assert storage.load("home.md") == old_home
    assert not storage.exists("replacement.md")


def test_zip_path_traversal_is_rejected(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "owned")

    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises(DocumentImportError, match="不安全路径"):
        extract_zip_safely(archive_path, destination, ImportLimits())

    assert not (tmp_path / "outside.txt").exists()


def test_zip_symlink_is_rejected(tmp_path):
    archive_path = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("APStackDoc/docs/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, "../../outside")

    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises(DocumentImportError, match="符号链接"):
        extract_zip_safely(archive_path, destination, ImportLimits())


def test_admin_document_import_page_is_available(admin_client):
    response = admin_client.get("/-/admin/document_import")
    assert response.status_code == 200
    page = response.data.decode()
    assert CONFIRMATION_TEXT in page
    assert 'id="document-import-overlay"' in page
    assert 'class="progress-bar progress-bar-animated"' in page
    assert 'id="document-import-form"' in page
    assert "pageWrapper.inert = true" in page


def test_document_import_page_rejects_non_admin(other_client):
    assert other_client.get("/-/admin/document_import").status_code == 403
    assert (
        other_client.post("/-/admin/document_import", data={}).status_code
        == 403
    )


def test_confirmation_failure_keeps_repository(admin_client, app_with_user):
    storage = app_with_user.storage
    old_commit = storage.repo.head.commit.hexsha

    response = admin_client.post(
        "/-/admin/document_import",
        data={"confirmation": "wrong", "source_directory": "C:/missing"},
    )

    assert response.status_code == 400
    assert storage.repo.head.commit.hexsha == old_commit
    assert storage.exists("home.md")


def test_admin_can_rebuild_from_server_directory(
    admin_client, app_with_user, tmp_path
):
    source = _write_apstack_source(tmp_path / "APStackDoc")

    response = admin_client.post(
        "/-/admin/document_import",
        data={
            "confirmation": CONFIRMATION_TEXT,
            "source_directory": str(source),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "文档仓库重建完成" in response.data.decode()
    assert app_with_user.storage.exists("apstack6.md")
    assert len(app_with_user.storage.log()) == 1


def test_admin_can_rebuild_from_zip(admin_client, app_with_user, tmp_path):
    source = _write_apstack_source(tmp_path / "APStackDoc")
    archive = _zip_source(source)

    response = admin_client.post(
        "/-/admin/document_import",
        data={
            "confirmation": CONFIRMATION_TEXT,
            "archive": (archive, "测试文档.zip"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    page = response.data.decode()
    assert "文档仓库重建完成" in page
    assert "测试文档.zip" in page
    assert app_with_user.storage.exists("apstack6.md")
    assert len(app_with_user.storage.log()) == 1
