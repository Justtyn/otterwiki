#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""
otterwiki.migrations

版本化、可重复执行的数据库迁移。

- 通过 `flask db upgrade` 在应用容器内执行，无需编译机安装 Python；
- 每个版本只会执行一次，版本号记录在 schema_version 表中；
- 重复执行不会重新添加已被管理员移除的组成员；
- 升级前请按 docs/multi-space-upgrade.md 停止应用并备份数据库与文档目录。
"""

from datetime import UTC, datetime

from sqlalchemy import inspect, text

from otterwiki.server import app, db
from otterwiki.models import (
    Group,
    GroupSpaceAuth,
    SchemaVersion,
    Space,
    User,
    UserGroup,
)

# 默认阅读组名称（迁移 v1 创建）
DEFAULT_GROUP_NAME = "默认阅读组"

MIGRATIONS = {}


def migration(version):
    def _register(fn):
        MIGRATIONS[version] = fn
        return fn

    return _register


def _applied_versions():
    return {v.version for v in SchemaVersion.query.all()}


def _ensure_draft_space_column():
    """在迁移的同一连接及事务中检查、更新旧表。"""
    connection = db.session.connection()
    columns = {c["name"] for c in inspect(connection).get_columns("drafts")}
    if "space_id" not in columns:
        connection.execute(
            text("ALTER TABLE drafts ADD COLUMN space_id INTEGER")
        )


def _backfill_drafts():
    from otterwiki.spaces import ensure_default_space

    default_space = ensure_default_space(commit=False)
    db.session.execute(
        text("UPDATE drafts SET space_id = :sid WHERE space_id IS NULL"),
        {"sid": default_space.id},
    )
    return default_space


@migration(1)
def _migrate_v1():
    """v1：多空间与用户组基础结构。

    - 创建缺失的数据表（空间、组、成员关系、授权关系、版本表）；
    - 为 drafts 补充 space_id 列（幂等）；
    - 创建默认空间与默认阅读组，将升级前具备阅读资格的现有本地用户
      加入该组并授权默认空间；
    - 将历史草稿归入默认空间。

    成员只做增量添加：后续管理员移除的成员不会因重复执行而回填。
    """
    # 1. 创建缺失的数据表
    db.metadata.create_all(
        bind=db.session.connection(),
        tables=[
            table
            for table in db.metadata.sorted_tables
            if table.name != "document_import_task"
        ],
    )

    # 2. drafts.space_id 列（对旧库幂等补充）
    _ensure_draft_space_column()

    # 3. 默认空间
    from otterwiki.spaces import ensure_default_space

    default_space = ensure_default_space(commit=False)

    # 4. 默认阅读组
    group = Group.query.filter_by(name=DEFAULT_GROUP_NAME).first()
    if group is None:
        group = Group(
            name=DEFAULT_GROUP_NAME,
            description="拥有默认空间阅读权限的用户组（由数据库迁移创建）。",
            created_at=datetime.now(UTC),
        )
        db.session.add(group)
        db.session.flush()

    # 5. 播种成员：升级前具备阅读资格的现有本地用户（只增不减）
    read_access = str(app.config.get("READ_ACCESS", "ANONYMOUS")).upper()
    existing_member_ids = {
        row[0]
        for row in db.session.query(UserGroup.user_id)
        .filter_by(group_id=group.id)
        .all()
    }
    added = 0
    for user in User.query.all():
        qualifies = (
            user.is_admin
            or (user.is_approved and user.allow_read)
            or read_access in ("ANONYMOUS", "REGISTERED")
            or (read_access == "APPROVED" and user.is_approved)
        )
        if not qualifies or user.id in existing_member_ids:
            continue
        db.session.add(UserGroup(user_id=user.id, group_id=group.id))
        added += 1
    db.session.flush()

    # 6. 授权默认组访问默认空间
    if (
        GroupSpaceAuth.query.filter_by(
            group_id=group.id, space_id=default_space.id
        ).first()
        is None
    ):
        db.session.add(
            GroupSpaceAuth(group_id=group.id, space_id=default_space.id)
        )
        db.session.flush()

    # 7. 历史草稿归入默认空间
    _backfill_drafts()

    app.logger.info(f"migrations: v1 完成，新增默认组成员 {added} 名。")
    print(f"  默认空间：{default_space.name}（{default_space.slug}）")
    print(f"  默认阅读组：{DEFAULT_GROUP_NAME}，本次新增成员 {added} 名。")


@migration(2)
def _migrate_v2():
    """v2：兼容早期实例的 drafts.space_id 补齐。

    部分实例的表结构由启动时的 db.create_all() 自动创建：
    create_all 只补缺失的表、不修改已存在的表，因此早期建过 drafts 表
    的库一直没有 space_id 列；同时这些库可能已记录 schema_version=1
    （旧版 v1 实现未包含 ALTER），导致维护工具等草稿查询直接 500。
    """
    _ensure_draft_space_column()
    _backfill_drafts()


@migration(3)
def _migrate_v3():
    """清理旧版本留下的孤立授权；不重建组或重新授予任何用户权限。"""
    _ensure_draft_space_column()
    _backfill_drafts()
    UserGroup.query.filter(
        (~UserGroup.user_id.in_(db.session.query(User.id)))
        | (~UserGroup.group_id.in_(db.session.query(Group.id)))
    ).delete(synchronize_session=False)
    GroupSpaceAuth.query.filter(
        (~GroupSpaceAuth.group_id.in_(db.session.query(Group.id)))
        | (~GroupSpaceAuth.space_id.in_(db.session.query(Space.id)))
    ).delete(synchronize_session=False)


@migration(4)
def _migrate_v4():
    """创建后台导入任务表；与版本记录使用同一事务。"""
    from otterwiki.models import DocumentImportTask

    DocumentImportTask.__table__.create(
        bind=db.session.connection(), checkfirst=True
    )


def run_migrations():
    """逐版本原子提交；当前版本失败时回滚，之前成功的版本保留。"""
    with app.app_context():
        for version in sorted(MIGRATIONS):
            try:
                connection = db.session.connection()
                if connection.dialect.name == "sqlite":
                    # sqlite3 的传统事务模式不会自动为 DDL 开启事务。
                    # 显式 BEGIN 使建表、ALTER、数据与版本号一起回滚，
                    # 同时在检查版本前取得写锁，防止两个迁移进程重复执行。
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                SchemaVersion.__table__.create(
                    bind=connection, checkfirst=True
                )
                if version in _applied_versions():
                    db.session.commit()
                    print(f"迁移 v{version}：已应用，跳过。")
                    continue
                print(f"迁移 v{version}：执行中……")
                MIGRATIONS[version]()
                db.session.add(
                    SchemaVersion(
                        version=version, applied_at=datetime.now(UTC)
                    )
                )
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.exception(f"migrations: v{version} 失败")
                print(
                    f"迁移 v{version} 失败：{e}\n"
                    "当前版本的结构、数据及版本记录已回滚；此前成功的"
                    "版本仍保留。请排查后重试，或按升级指南恢复完整备份。"
                )
                raise
        print("数据库迁移完成。")
