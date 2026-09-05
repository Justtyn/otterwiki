#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""
otterwiki.spaces

多空间运行时支持：

- SpacePrefixMiddleware 把 /-/s/<空间标识>/... 形式的请求挂载为
  对应空间上下文下的普通路径（通过 WSGI SCRIPT_NAME 实现，
  url_for 生成的链接会自动携带空间前缀）；
- space_access_gate 在页面内容加载之前完成空间阅读授权校验，
  匿名访问进入登录流程，未授权或已归档空间统一返回 404；
- 存储解析入口见 otterwiki.gitstorage.SpaceStorageProxy，
  禁止通过修改全局仓库路径来切换空间。

默认空间继续使用 REPOSITORY 配置的现有仓库，其余空间使用
SPACES_ROOT/<空间ID>/repository 下的独立 git 仓库。
"""

import os
import re
from datetime import UTC, datetime

from flask import abort, g, redirect, request, url_for
from flask_login import current_user

from otterwiki.models import (
    SPACE_SLUG_RE_STR,
    GroupSpaceAuth,
    Space,
    UserGroup,
)
from otterwiki.server import app, db

# 空间地址标识匹配：/-/s/<slug> 或 /-/s/<slug>/剩余路径
_SPACE_URL_RE = re.compile(r"^/-/s/([^/]+)(/.*)?$")

SPACE_SLUG_RE = re.compile(SPACE_SLUG_RE_STR)

# 默认空间的地址标识（保留字，其他空间不可使用）
DEFAULT_SPACE_SLUG = "default"
DEFAULT_SPACE_NAME = "主空间"

# 空间路径下不重写的剩余路径前缀：Git HTTP 仅服务默认仓库
_UNREWRITTEN_REST_PREFIXES = ("/.git",)

# 站内全局路径：这些链接不随当前空间添加前缀（账号、管理、静态资源等）
_GLOBAL_URL_PREFIXES = (
    "/-/login",
    "/-/logout",
    "/-/register",
    "/-/lost_password",
    "/-/confirm_email",
    "/-/recover_password",
    "/-/request_confirmation_link",
    "/-/settings",
    "/-/user",
    "/-/housekeeping",
    "/-/admin",
    "/-/interface",
    "/-/healthz",
    "/-/plugin-static.css",
    "/-/api/v1/pull",
    "/static/",
    "/.git",
    "/robots.txt",
    "/sitemap.xml",
    "/favicon.ico",
    "/manifest.webmanifest",
)

# 不参与空间阅读门禁的端点：账号流程、技术端点、Git HTTP 与 Webhook。
# 其余端点（正文、源码、附件、缩略图、历史、差异、搜索、预览、接口、
# 订阅源等）一律要求“已登录 + 满足空间授权”。
SPACE_EXEMPT_ENDPOINTS = frozenset(
    {
        "login",
        "logout",
        "register",
        "lost_password",
        "confirm_email",
        "recover_password",
        "request_confirmation_link",
        "settings",
        "user",
        "healthz",
        "robotstxt",
        "favicon",
        "webmanifest",
        "well_known_change_password",
        "dotgit",
        "git_info_refs",
        "git_upload_pack",
        "git_receive_pack",
        "pull_webhook",
        "static",
        "plugin_static_css",
    }
)


#
# 空间行相关辅助
#


def is_valid_space_slug(slug):
    """空间地址标识合法性：小写字母/数字开头，仅含小写字母、数字、连字符。"""
    if not slug or not SPACE_SLUG_RE.match(slug):
        return False
    if slug == DEFAULT_SPACE_SLUG:
        # default 是默认空间的保留标识
        return False
    return True


def get_default_space():
    return Space.query.filter_by(is_default=True).first()


def get_space_by_slug(slug):
    return Space.query.filter_by(slug=slug).first()


def ensure_default_space(commit=True):
    """确保默认空间行存在（幂等）。只保证空间行，不播种组成员。"""
    space = get_default_space()
    if space is not None:
        return space
    now = datetime.now(UTC)
    space = Space(
        slug=DEFAULT_SPACE_SLUG,
        name=DEFAULT_SPACE_NAME,
        description=None,
        home_page=None,
        is_archived=False,
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db.session.add(space)
    db.session.flush()
    if commit:
        db.session.commit()
    app.logger.info("spaces: 已创建默认空间。")
    return space


def space_slug_exists(slug):
    """WSGI 中间件用：判断空间标识是否存在（存在才重写请求路径）。"""
    try:
        with app.app_context():
            row = db.session.query(Space.id).filter_by(slug=slug).first()
            return row is not None
    except Exception as e:
        app.logger.error(f"spaces: 查询空间标识 '{slug}' 失败: {e}")
        # 出错时按“不存在”处理：请求落到兜底 404，不会泄露任何内容
        return False


#
# 请求级空间上下文
#


def resolve_current_space():
    """解析当前请求的空间并写入 g.space（before_request 调用）。"""
    slug = request.environ.get("otterwiki.space_slug")
    space = None
    if slug:
        space = get_space_by_slug(slug)
    if space is None:
        space = get_default_space()
    g.space = space
    return space


def current_space():
    try:
        return getattr(g, "space", None)
    except RuntimeError:
        # 无请求上下文（命令行、后台任务）
        return None


def current_space_id():
    space = current_space()
    return space.id if space is not None else None


def current_space_key():
    """当前空间的缓存键前缀：默认空间用 "default"，其余用地址标识。"""
    space = current_space()
    if space is None or space.is_default:
        return "default"
    return space.slug


#
# 空间仓库路径与存储解析
#


def get_spaces_root():
    """新空间仓库的根目录：SPACES_ROOT 配置优先，否则取 REPOSITORY 同级 spaces 目录。"""
    configured = app.config.get("SPACES_ROOT")
    if configured:
        return configured
    repository = app.config.get("REPOSITORY") or os.getcwd()
    return os.path.join(os.path.dirname(os.path.abspath(repository)), "spaces")


def space_repository_path(space):
    if space is None or space.is_default:
        return app.config["REPOSITORY"]
    return os.path.join(get_spaces_root(), str(space.id), "repository")


def get_space_storage(space):
    """返回指定空间的具体 GitStorage 实例（按需创建目录并初始化 git 仓库）。"""
    from otterwiki.gitstorage import GitStorage

    path = space_repository_path(space)
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    initialize = not os.path.exists(os.path.join(path, ".git"))
    return GitStorage(path, initialize=initialize)


def current_storage():
    """当前请求上下文对应空间的具体 GitStorage 实例。

    后台任务必须显式接收本函数返回的仓库对象，不得依赖请求上下文，
    也不得修改全局仓库路径。
    """
    from otterwiki.server import storage

    return storage.resolve_storage()


def reset_space_caches():
    """清空空间存储实例缓存（测试夹具与仓库重建后使用）。"""
    from otterwiki.server import storage

    storage.reset_space_cache()


#
# 权限判定
#


def user_group_ids(user_id):
    return [
        row[0]
        for row in db.session.query(UserGroup.group_id)
        .filter(UserGroup.user_id == user_id)
        .all()
    ]


def space_read_allowed(space, user):
    """普通用户的空间阅读判定：已登录且至少一个所属组获准访问该空间。

    全局阅读条件（READ_ACCESS 等）仍由既有的 has_permission("READ")
    在视图层负责，这里只做空间维度的授权并集判断。每次请求实时查询，
    撤销成员或授权后从下一次请求开始生效。
    """
    if space is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    if space.is_archived:
        return False
    user_id = getattr(user, "id", None)
    if user_id is None:
        # 代理请求头登录等没有数据库账号的用户不参与空间授权
        return False
    group_ids = user_group_ids(user_id)
    if not group_ids:
        return False
    row = (
        db.session.query(GroupSpaceAuth.space_id)
        .filter(
            GroupSpaceAuth.group_id.in_(group_ids),
            GroupSpaceAuth.space_id == space.id,
        )
        .first()
    )
    return row is not None


def accessible_spaces(user):
    """用户可访问的未归档空间列表；管理员可见全部未归档空间。"""
    if not getattr(user, "is_authenticated", False):
        return []
    query = Space.query.filter(Space.is_archived.isnot(True))
    if getattr(user, "is_admin", False):
        spaces = query.all()
    else:
        user_id = getattr(user, "id", None)
        if user_id is None:
            return []
        group_ids = user_group_ids(user_id)
        if not group_ids:
            return []
        space_ids = [
            row[0]
            for row in db.session.query(GroupSpaceAuth.space_id)
            .filter(GroupSpaceAuth.group_id.in_(group_ids))
            .all()
        ]
        if not space_ids:
            return []
        spaces = query.filter(Space.id.in_(space_ids)).all()
    # 默认空间排最前，其余按名称排序
    spaces.sort(key=lambda s: (0 if s.is_default else 1, s.name or ""))
    return spaces


#
# URL 辅助
#


def full_path_with_space():
    """当前请求的完整路径（含空间前缀与查询串）。

    空间前缀通过 WSGI SCRIPT_NAME 实现，request.full_path 只有重写后的
    路径，登录跳转的 next 需要把它拼回去。
    """
    prefix = request.environ.get("SCRIPT_NAME", "")
    return prefix + request.full_path


def login_redirect():
    """匿名访问受空间保护的内容时进入登录流程，并保留完整跳转目标。"""
    return redirect(url_for("login", next=full_path_with_space()))


def space_prefixed_url(url):
    """非默认空间上下文中，为站内文档链接补充当前空间地址前缀。

    外部链接、协议相对地址、锚点、静态资源、账号/全局管理入口以及
    已明确指定空间的链接（/-/s/...）保持原意。
    """
    space = current_space()
    if space is None or space.is_default:
        return url
    if not isinstance(url, str) or not url.startswith("/"):
        return url
    if url.startswith("//"):
        return url
    prefix = f"/-/s/{space.slug}"
    if url == prefix or url.startswith(prefix + "/"):
        return url
    if url.startswith("/-/s/"):
        # 明确指定其他空间的链接保持原意
        return url
    for global_prefix in _GLOBAL_URL_PREFIXES:
        if url.startswith(global_prefix):
            return url
    return prefix + url


#
# WSGI 中间件
#


class SpacePrefixMiddleware:
    """把 /-/s/<空间标识>/... 的请求挂载为该空间上下文下的普通路径。

    通过 WSGI SCRIPT_NAME 实现，Flask 的 url_for 会自动在生成的链接前
    带上空间前缀。仅当空间标识真实存在时才重写；其余情况保持原路径，
    由兜底路由统一返回 404（匿名进入登录流程）。
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/-/s/") and not path.startswith("/-/s//"):
            match = _SPACE_URL_RE.match(path)
            if match is not None:
                slug = match.group(1)
                rest = match.group(2) or "/"
                if not rest.startswith(_UNREWRITTEN_REST_PREFIXES):
                    if space_slug_exists(slug):
                        environ["otterwiki.space_slug"] = slug
                        environ["SCRIPT_NAME"] = f"/-/s/{slug}"
                        environ["PATH_INFO"] = rest
        return self.wsgi_app(environ, start_response)


#
# 请求级空间门禁
#


@app.before_request
def space_access_gate():
    """空间阅读门禁：在任何文档及元数据加载之前完成校验。"""
    space = resolve_current_space()
    endpoint = request.endpoint
    # 以 admin 开头的端点是全局管理路由（管理后台、空间/组管理、用户管理等），
    # 由各自的 has_permission("ADMIN") 检查保护，不属于空间内容
    if (
        endpoint is None
        or endpoint in SPACE_EXEMPT_ENDPOINTS
        or endpoint.startswith("admin")
    ):
        return None
    user = current_user._get_current_object()
    if not user.is_authenticated:
        # 未登录的网页访问进入登录流程
        return login_redirect()
    if not space_read_allowed(space, user):
        # 无授权或已归档空间对普通用户统一返回 404
        abort(404)
    return None


#
# 模板全局
#


@app.context_processor
def _inject_space_globals():
    from otterwiki.preferences import get_document_font_size

    try:
        spaces_list = accessible_spaces(current_user)
    except Exception as e:
        app.logger.warning(f"spaces: 获取可访问空间列表失败: {e}")
        spaces_list = []
    try:
        font_size = get_document_font_size()
    except Exception as e:
        app.logger.warning(f"spaces: 获取文档字号失败: {e}")
        font_size = 15
    return {
        "current_space": current_space(),
        "accessible_spaces": spaces_list,
        "document_font_size": font_size,
    }


# 空间路径前缀中间件（模块只会被导入一次）
app.wsgi_app = SpacePrefixMiddleware(app.wsgi_app)

# 多空间权限基于内置账户与会话校验。代理头认证（PROXY_HEADER）不经过
# 登录流程，无法纳入空间授权模型，明确不支持：启动时告警，不提供静默绕过。
if app.config.get("AUTH_METHOD") == "PROXY_HEADER":
    app.logger.warning(
        "spaces: AUTH_METHOD='PROXY_HEADER' 与多空间权限不兼容，不受支持："
        "空间访问需要内置账户登录并由用户组授权。"
        "请改用 SIMPLE（内置注册/登录）方式管理用户。"
    )

# 确保默认空间行存在（建表之后；只创建空间行，不播种组成员）
with app.app_context():
    ensure_default_space()

# vim: set et ts=8 sts=4 sw=4 ai:

# 在空间授权完成后执行维护门禁。
from otterwiki.import_tasks import install_request_guards

install_request_guards(app)
