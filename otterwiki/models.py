#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

from otterwiki.server import db
from datetime import datetime, UTC

__all__ = [
    'Preferences',
    'Drafts',
    'User',
    'Cache',
    'Space',
    'Group',
    'UserGroup',
    'GroupSpaceAuth',
    'SchemaVersion',
]


class TimeStamp(db.types.TypeDecorator):
    # thanks to https://mike.depalatis.net/blog/sqlalchemy-timestamps.html
    impl = db.types.DateTime
    LOCAL_TIMEZONE = datetime.now(UTC).astimezone().tzinfo
    cache_ok = True

    def process_bind_param(self, value: datetime, dialect):
        if value.tzinfo is None:
            value = value.astimezone(self.LOCAL_TIMEZONE)

        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)


class Preferences(db.Model):
    name = db.Column(db.String(256), primary_key=True)
    value = db.Column(db.Text)

    def __str__(self):
        return '{}: {}'.format(self.name, self.value)


class Drafts(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pagepath = db.Column(db.String(2048), index=True)
    revision = db.Column(db.String(64))
    author_email = db.Column(db.String(256))
    content = db.Column(db.Text)
    cursor_line = db.Column(db.Integer)
    cursor_ch = db.Column(db.Integer)
    datetime = db.Column(TimeStamp())
    # 草稿所属空间；NULL 表示升级前的历史草稿（迁移时回填为默认空间）
    space_id = db.Column(db.Integer, nullable=True)

    def __str__(self):
        return f"<Draft id={self.id} pagepath={self.pagepath} author={self.author_email} datetime={self.datetime}>"


# 空间地址标识的合法性约束：小写字母/数字开头，仅含小写字母、数字与连字符。
# default 为默认空间保留字。
SPACE_SLUG_RE_STR = r"^[a-z0-9][a-z0-9-]{0,63}$"


class Space(db.Model):
    __tablename__ = "space"
    id = db.Column(db.Integer, primary_key=True)
    # 地址标识：创建后固定，不可修改
    slug = db.Column(db.String(64), index=True, unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # 空间首页的页面路径；空值表示使用 Home
    home_page = db.Column(db.String(2048), nullable=True)
    is_archived = db.Column(db.Boolean(), default=False)
    is_default = db.Column(db.Boolean(), default=False)
    created_at = db.Column(TimeStamp(), default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        TimeStamp(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __str__(self):
        return f"<Space id={self.id} slug={self.slug} name={self.name}>"


class Group(db.Model):
    __tablename__ = "group"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(TimeStamp(), default=lambda: datetime.now(UTC))

    def __str__(self):
        return f"<Group id={self.id} name={self.name}>"


# 用户与用户组的关系
class UserGroup(db.Model):
    __tablename__ = "user_group"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    group_id = db.Column(
        db.Integer, db.ForeignKey("group.id"), primary_key=True
    )


# 用户组与空间的阅读授权关系
class GroupSpaceAuth(db.Model):
    __tablename__ = "group_space_auth"
    group_id = db.Column(
        db.Integer, db.ForeignKey("group.id"), primary_key=True
    )
    space_id = db.Column(
        db.Integer, db.ForeignKey("space.id"), primary_key=True
    )


class SchemaVersion(db.Model):
    """记录已执行的数据库迁移版本，保证迁移可重复执行且不会重复播种。"""

    __tablename__ = "schema_version"
    version = db.Column(db.Integer, primary_key=True)
    applied_at = db.Column(TimeStamp(), default=lambda: datetime.now(UTC))


class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128))
    email = db.Column(db.String(128), index=True, unique=True)
    password_hash = db.Column(db.String(512))
    first_seen = db.Column(TimeStamp())
    last_seen = db.Column(TimeStamp())
    is_approved = db.Column(db.Boolean(), default=False)
    is_admin = db.Column(db.Boolean(), default=False)
    email_confirmed = db.Column(db.Boolean(), default=False)
    allow_read = db.Column(db.Boolean(), default=False)
    allow_write = db.Column(db.Boolean(), default=False)
    allow_upload = db.Column(db.Boolean(), default=False)

    def __repr__(self):
        permissions = ""
        if self.allow_read:
            permissions += "R"
        if self.allow_write:
            permissions += "W"
        if self.allow_upload:
            permissions += "U"
        if self.is_admin:
            permissions += "A"
        return f"<User {self.id} '{self.name} <{self.email}>' {permissions}>"


class Cache(db.Model):
    __tablename__ = "cache"
    key = db.Column(db.String(64), index=True, primary_key=True)
    value = db.Column(db.Text)
    datetime = db.Column(TimeStamp())


class DocumentImportTask(db.Model):
    """持久化导入状态；内部工作路径不向 API 暴露。"""

    __tablename__ = "document_import_task"
    id = db.Column(db.String(32), primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    space_id = db.Column(db.Integer, nullable=False, index=True)
    request_key = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    phase = db.Column(db.String(32), nullable=False, default="queued")
    completed = db.Column(db.BigInteger, nullable=False, default=0)
    total = db.Column(db.BigInteger, nullable=True)
    extracted_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    created_at = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.Float, nullable=False)
    started_at = db.Column(db.Float, nullable=True)
    finished_at = db.Column(db.Float, nullable=True)
    result = db.Column(db.JSON, nullable=True)
    error = db.Column(db.Text, nullable=True)
    workspace = db.Column(db.Text, nullable=False)
    checkpoint = db.Column(db.JSON, nullable=True)
    __table_args__ = (
        db.UniqueConstraint("user_id", "space_id", "request_key"),
    )
