#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""不依赖 Flask/数据库的导入锁、请求排空及仓库切换日志。"""

import json
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager

_condition = threading.Condition(threading.RLock())
_readers = {}
_process_locks = set()
_process_locks_guard = threading.Lock()


def task_root(config):
    configured = config.get("DOCUMENT_IMPORT_TASK_ROOT")
    return Path(
        configured
        or (
            Path(config["REPOSITORY"]).resolve().parent
            / ".otterwiki-import-tasks"
        )
    )


class ProcessLock:
    """持有打开的文件描述符，进程退出后由操作系统释放锁。"""

    def __init__(self, root):
        self.root = Path(root)
        self.file = None
        self.key = str((self.root / "executor.lock").resolve())

    def acquire(self):
        # flock 在部分平台按“进程”而不是按文件描述符计数，同一 Web
        # 进程的轮询线程可能再次加锁成功。先用进程内集合补齐这层互斥。
        with _process_locks_guard:
            if self.key in _process_locks:
                return False
            _process_locks.add(self.key)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            file = (self.root / "executor.lock").open("a+b")
            if os.name == "nt":
                import msvcrt

                file.seek(0, 2)
                if file.tell() == 0:
                    file.write(b"0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            if "file" in locals():
                file.close()
            with _process_locks_guard:
                _process_locks.discard(self.key)
            return False
        self.file = file
        return True

    def release(self):
        if self.file is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None
                with _process_locks_guard:
                    _process_locks.discard(self.key)


def write_journal(root, data):
    """先写临时文件并刷盘，再原子替换，避免半份 JSON。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / (data["id"] + ".json")
    temporary = path.with_suffix(".tmp")
    with _condition:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _condition.notify_all()


def remove_journal(root, task_id):
    """终态任务不再需要崩溃恢复文件，避免每次请求扫描历史任务。"""
    path = Path(root) / (task_id + ".json")
    with _condition:
        path.unlink(missing_ok=True)
        _condition.notify_all()


def journals(root):
    for path in Path(root).glob("*.json"):
        # 损坏的日志不能当成没有维护任务，应阻止导入并提示人工检查。
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            yield {
                "id": path.stem,
                "target": "*",
                "status": "interrupted",
                "maintenance": True,
                "_corrupt": True,
                "error": (
                    "导入检查点损坏，无法确认仓库完整性，已保持维护；"
                    "请按升级指南人工恢复。"
                ),
            }


def blocked(root, target):
    target = str(Path(target).resolve())
    return any(
        j.get("maintenance") and j["target"] in (target, "*")
        for j in journals(root)
    )


def enter_repository(root, target):
    """维护检查与活动请求登记在同一锁内完成，返回幂等释放函数。"""
    key = str(Path(target).resolve())
    with _condition:
        if blocked(root, key):
            return None
        _readers[key] = _readers.get(key, 0) + 1
    released = False

    def release():
        nonlocal released
        with _condition:
            if not released:
                released = True
                _readers[key] -= 1
                if not _readers[key]:
                    del _readers[key]
                _condition.notify_all()

    return release


def wait_for_readers(target, timeout=60):
    key = str(Path(target).resolve())
    deadline = time.monotonic() + timeout
    with _condition:
        while _readers.get(key, 0):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "等待空间已有请求结束超过 60 秒，未修改仓库。"
                )
            _condition.wait(remaining)


@contextmanager
def repository_operation(config, target):
    """供 Git 同步等非 HTTP 操作共用维护门禁。"""
    release = enter_repository(task_root(config), target)
    if release is None:
        raise RuntimeError("当前空间正在导入维护，暂时无法执行 Git 操作。")
    try:
        yield
    finally:
        release()


def repo_commit(path):
    import git

    try:
        with git.Repo(path) as repo:
            return repo.head.commit.hexsha
    except (OSError, ValueError, git.GitError):
        return None


def recover_journal(root, data):
    """调用方必须持有执行锁；恢复目录缺口，不自动重复导入。"""
    target = Path(data["target"])
    backup = Path(data["backup"]) if data.get("backup") else None
    expected = data.get("commit")
    data["status"] = "interrupted"
    if data.get("phase") not in ("switching", "finishing", "succeeded") and (
        data.get("old_commit") and repo_commit(target) == data["old_commit"]
    ):
        data["maintenance"] = False
        data["error"] = (
            "任务已中断，原仓库未替换；请重新选择导入源并确认后重试。"
        )
    elif not target.exists() and backup and repo_commit(backup):
        os.replace(backup, target)
        data["maintenance"] = False
        data["error"] = (
            "任务在切换时中断，已恢复旧仓库；请核对文档后重新导入。"
        )
    elif expected and repo_commit(target) == expected:
        data["maintenance"] = False
        data["error"] = (
            "仓库已切换，但任务未完整结束。请核对文档、附件和草稿，勿直接重复导入。"
        )
    elif repo_commit(target) and data.get("old_commit") == repo_commit(target):
        data["maintenance"] = False
        data["error"] = "任务已中断，当前仍为原仓库；请核对后重新导入。"
    else:
        data["maintenance"] = True
        data["error"] = (
            "仓库切换现场需要人工恢复，已保持维护；请保留任务日志和备份并按升级指南处理。"
        )
    write_journal(root, data)
    return data


def recover_before_storage(config):
    """在任何 GitStorage 初始化前修复中断的目录切换。"""
    root = task_root(config)
    if not root.exists():
        return
    lock = ProcessLock(root)
    if not lock.acquire():
        return
    try:
        for data in journals(root):
            if data.get("_corrupt"):
                continue
            if data.get("status") in ("queued", "running") or (
                data.get("status") == "interrupted" and data.get("maintenance")
            ):
                recover_journal(root, data)
    finally:
        lock.release()


if __name__ == "__main__":
    # 容器入口仅加载配置并恢复文件现场，不初始化 Flask 应用或数据库。
    from flask.config import Config

    config = Config(os.getcwd())
    config.update(
        REPOSITORY=os.environ.get(
            "OTTERWIKI_REPOSITORY", "/app-data/repository"
        ),
        DOCUMENT_IMPORT_TASK_ROOT=None,
    )
    config.from_envvar("OTTERWIKI_SETTINGS", silent=True)
    for name in ("REPOSITORY", "DOCUMENT_IMPORT_TASK_ROOT"):
        if name in os.environ:
            config[name] = os.environ[name]
    recover_before_storage(config)
    print("1" if blocked(task_root(config), config["REPOSITORY"]) else "0")
