#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

import datetime
import os
import sys
import logging

from flask import Flask
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

import otterwiki.gitstorage
import otterwiki.util
from otterwiki import __version__, fatal_error
from otterwiki.plugins import plugin_manager
from otterwiki.renderer import OtterwikiRenderer

app = Flask(__name__)
# Keep Chinese text readable in rendered JSON (for example flash messages
# embedded in the page) instead of escaping every character as ``\uXXXX``.
app.json.ensure_ascii = False
# default configuration settings
app.config.update(
    DEBUG=False,  # make sure DEBUG is off unless enabled explicitly otherwise
    TESTING=False,
    LOG_LEVEL="INFO",
    REPOSITORY=None,
    SECRET_KEY="CHANGE ME",
    SITE_NAME="An Otter Wiki",
    SITE_DESCRIPTION=None,
    SERVER_NAME=None,
    SITE_LOGO=None,
    SITE_ICON=None,
    SITE_LANG="zh-CN",
    HIDE_LOGO=False,
    OPEN_LINKS_IN_NEW_TAB=False,
    AUTH_METHOD="",
    AUTH_HEADERS_USERNAME="x-otterwiki-name",
    AUTH_HEADERS_EMAIL="x-otterwiki-email",
    AUTH_HEADERS_PERMISSIONS="x-otterwiki-permissions",
    AUTH_ROLES_READ="READ",
    AUTH_ROLES_WRITE="WRITE",
    AUTH_ROLES_UPLOAD="UPLOAD",
    AUTH_ROLES_ADMIN="ADMIN",
    READ_ACCESS="ANONYMOUS",
    WRITE_ACCESS="ANONYMOUS",
    ATTACHMENT_ACCESS="ANONYMOUS",
    AUTO_APPROVAL=True,
    DISABLE_REGISTRATION=False,
    EMAIL_NEEDS_CONFIRMATION=True,
    NOTIFY_ADMINS_ON_REGISTER=False,
    NOTIFY_USER_ON_APPROVAL=False,
    RETAIN_PAGE_NAME_CASE=False,
    SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
    MAIL_DEFAULT_SENDER="otterwiki@YOUR.ORGANIZATION.TLD",
    MAIL_SERVER="",
    MAIL_PORT="",
    MAIL_USERNAME="",
    MAIL_PASSWORD="",
    MAIL_USE_TLS=False,
    MAIL_USE_SSL=False,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MINIFY_HTML=True,
    SIDEBAR_MENUTREE_MODE="SORTED",
    SIDEBAR_MENUTREE_IGNORE_CASE=False,
    SIDEBAR_MENUTREE_MAXDEPTH="",
    SIDEBAR_MENUTREE_FOCUS="SUBTREE",  # OFF
    SIDEBAR_CUSTOM_MENU="",
    COMMIT_MESSAGE="REQUIRED",  # OPTIONAL DISABLED
    DEFAULT_COMMIT_MESSAGE="",
    GIT_WEB_SERVER=False,
    GIT_REMOTE_PUSH_ENABLED=False,
    GIT_REMOTE_PUSH_URL="",
    GIT_REMOTE_PUSH_PRIVATE_KEY="",
    GIT_REMOTE_PULL_ENABLED=False,
    GIT_REMOTE_PULL_URL="",
    GIT_REMOTE_PULL_URL_SECURE=False,
    GIT_REMOTE_PULL_PRIVATE_KEY="",
    SIDEBAR_SHORTCUTS="home pageindex createpage",
    ROBOTS_TXT="allow",
    WIKILINK_STYLE="",
    MAX_FORM_MEMORY_SIZE=1_000_000,
    HTML_EXTRA_HEAD="",
    HTML_EXTRA_BODY="",
    LOG_LEVEL_WERKZEUG="INFO",
    TREAT_UNDERSCORE_AS_SPACE_FOR_TITLES=False,
    HOME_PAGE="",
    RENDERER_HTML_ALLOWLIST="",
    ADMIN_USER_EMAIL="",
    SESSION_COOKIE_SAMESITE="Lax",
    SECURITY_HEADERS=True,
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_TIME_LIMIT=86400,
    APSTACK_IMPORT_MAX_ARCHIVE_SIZE=1024 * 1024 * 1024,
    APSTACK_IMPORT_MAX_EXTRACTED_SIZE=2 * 1024 * 1024 * 1024,
    APSTACK_IMPORT_MAX_FILES=50_000,
    DOCUMENT_IMPORT_TASK_ROOT=None,
    APSTACK_API_BASE_URL="http://localhost:9988",
    APSTACK_API_TIMEOUT=10,
    # 多空间：新空间仓库根目录；默认取 REPOSITORY 同级的 spaces 目录
    SPACES_ROOT=None,
    # 全局文档字号（px），允许 12-24 的整数
    DOCUMENT_FONT_SIZE=15,
)
app.config.from_envvar("OTTERWIKI_SETTINGS", silent=True)

# print current version
print(
    f"*** Starting An Otter Wiki {__version__} {os.getenv('GIT_TAG', '')}",
    file=sys.stderr,
)

# check if any config option exists as environment variable, remember
# which options were set via the environment, so that overrides by
# database preferences can be warned about in update_app_config()
config_from_environment = {}
for key in app.config:
    if key in os.environ:
        if type(app.config[key]) == bool:
            app.config[key] = os.environ[key].lower() in [
                "true",
                "yes",
                "on",
                "1",
            ]
        else:
            app.config[key] = os.environ[key]
        config_from_environment[key] = app.config[key]

# validate DOCUMENT_FONT_SIZE from settings file / environment
_document_font_size = None
try:
    _document_font_size = int(
        str(app.config.get("DOCUMENT_FONT_SIZE", "")).strip()
    )
except (TypeError, ValueError):
    _document_font_size = None
if _document_font_size is None or not 12 <= _document_font_size <= 24:
    app.logger.warning(
        'server: Ignored invalid DOCUMENT_FONT_SIZE={!r}. '
        'Must be an integer between 12 and 24. Falling back to 15.'.format(
            app.config.get("DOCUMENT_FONT_SIZE")
        )
    )
    _document_font_size = 15
app.config["DOCUMENT_FONT_SIZE"] = _document_font_size

# configure logging
app.logger.setLevel(app.config["LOG_LEVEL"])
logging.getLogger('werkzeug').setLevel(app.config["LOG_LEVEL_WERKZEUG"])

# setup database
db = SQLAlchemy(app)

# ensure SECRET_KEY is set
if (
    len(app.config["SECRET_KEY"]) < 16
    or app.config["SECRET_KEY"] == "CHANGE ME"
):
    fatal_error(
        "Please configure a random SECRET_KEY with a length of at least 16 characters."
    )

# enable CSRF protection globally
csrf = CSRFProtect(app)

# 初始化 GitStorage 前恢复中断导入留下的目录缺口。
from otterwiki.import_runtime import recover_before_storage, blocked, task_root

if app.config["REPOSITORY"]:
    recover_before_storage(app.config)

# setup storage
_import_maintenance = bool(app.config["REPOSITORY"]) and blocked(
    task_root(app.config), app.config["REPOSITORY"]
)
if _import_maintenance:
    default_storage = otterwiki.gitstorage.DeferredGitStorage(
        app.config["REPOSITORY"]
    )
    storage = otterwiki.gitstorage.SpaceStorageProxy(default_storage)
elif app.config["REPOSITORY"] is None:
    fatal_error("Please configure a REPOSITORY path.")
elif not os.path.exists(app.config["REPOSITORY"]):
    fatal_error(
        "Repository path '{}' not found. Please configure otterwiki.".format(
            app.config["REPOSITORY"]
        )
    )
else:
    try:
        default_storage = otterwiki.gitstorage.GitStorage(
            app.config["REPOSITORY"]
        )
    except otterwiki.gitstorage.StorageError as e:
        fatal_error(e)
    # 多空间：统一存储解析入口。storage 是按请求空间转发的代理对象，
    # 切换空间通过请求上下文完成，禁止修改全局仓库路径。
    storage = otterwiki.gitstorage.SpaceStorageProxy(default_storage)


# make sure SERVER_NAME is None if empty (to make url_for work)
if otterwiki.util.empty(app.config["SERVER_NAME"]):
    app.config["SERVER_NAME"] = None

# check if the git repository is empty
if (
    not _import_maintenance
    and (len(storage.list()[0]) < 1)
    and (  # pyright: ignore never unbound
        len(storage.log()) < 1  # pyright: ignore
    )
):
    home_page_config = app.config.get("HOME_PAGE", "")

    # only create initial page if HOME_PAGE is empty or doesn't start with /-/
    if not home_page_config or not home_page_config.startswith("/-/"):
        with open(os.path.join(app.root_path, "initial_home.md")) as f:
            content = f.read()

            if not home_page_config:
                # use the default Home page
                filename = (
                    "Home.md"
                    if app.config["RETAIN_PAGE_NAME_CASE"]
                    else "home.md"
                )
            else:
                # use the custom page path from HOME_PAGE
                custom_path = home_page_config.strip("/")
                if app.config["RETAIN_PAGE_NAME_CASE"]:
                    filename = f"{custom_path}.md"
                else:
                    filename = f"{custom_path.lower()}.md"

            storage.store(  # pyright: ignore
                filename=filename,
                content=content,
                author=("Otterwiki Robot", "noreply@otterwiki"),
                message="Initial commit",
            )
            app.logger.info(f"server: Created initial page /{filename[:-3]}.")


#
# app.config from db preferences
#
from otterwiki.models import *

mail = None


def update_app_config():
    global mail
    with app.app_context():
        for item in Preferences.query:
            if item.name.upper() == "DOCUMENT_FONT_SIZE":
                # 文档字号按请求从数据库读取（保证多进程一致），
                # app.config 中仅保留配置文件/环境变量的默认值
                continue
            if item.name.upper() in [
                "MAIL_USE_TLS",
                "MAIL_USE_SSL",
                "DISABLE_REGISTRATION",
                "AUTO_APPROVAL",
                "EMAIL_NEEDS_CONFIRMATION",
                "NOTIFY_ADMINS_ON_REGISTER",
                "NOTIFY_USER_ON_APPROVAL",
                "RETAIN_PAGE_NAME_CASE",
                "SIDEBAR_MENUTREE_IGNORE_CASE",
                "GIT_WEB_SERVER",
                "GIT_REMOTE_PUSH_ENABLED",
                "GIT_REMOTE_PULL_ENABLED",
                "GIT_REMOTE_PULL_URL_SECURE",
                "HIDE_LOGO",
                "OPEN_LINKS_IN_NEW_TAB",
                "TREAT_UNDERSCORE_AS_SPACE_FOR_TITLES",
            ] or item.name.upper().startswith("SIDEBAR_SHORTCUT_"):
                item.value = item.value.lower() in ["true", "yes"]
            if item.name.upper() in ["MAIL_PORT"]:
                try:
                    item.value = int(item.value)
                except ValueError:
                    app.logger.warning(
                        "server: Ignored invalid value app.config[\"{}\"]={}".format(
                            item.name, item.value
                        )
                    )
            # warn if a database preference overrides a value that was
            # set via an environment variable
            if item.name in config_from_environment and str(item.value) != str(
                config_from_environment[item.name]
            ):
                app.logger.warning(
                    "server: app.config[\"{}\"] set via environment"
                    " variable is overridden by the database"
                    " preference".format(item.name)
                )
            # update app settings
            app.config[item.name] = item.value
        # setup flask_mail
        mail = Mail(app)


with app.app_context():
    # 导入任务表必须由 v4 原子建表，不能在升级命令加载应用时提前提交。
    db.metadata.create_all(
        bind=db.engine,
        tables=[
            table
            for table in db.metadata.sorted_tables
            if table.name != "document_import_task"
        ],
    )

    # 已有表的结构变更仅由 flask db upgrade 执行，不能在加载应用时
    # 提前提交；否则迁移失败时无法回滚，也会让多个工作进程竞争 ALTER。
update_app_config()


#
# a renderer configured with the app.config
#
# initialize renderer
app_renderer = OtterwikiRenderer(config=app.config)


#
# plugins
#
plugininfo = plugin_manager.list_plugin_distinfo()
for plugin, dist in plugininfo:
    app.logger.info(
        f"server: Loaded plugin: {dist.project_name}-{dist.version}"
    )
# setup plugins
plugin_manager.hook.setup(
    app=app, storage=storage, db=db  # pyright: ignore never unbound
)


#
# template extensions
#
@app.template_filter("debug_unixtime")
def template_debug_unixtime(s: int) -> str:
    if app.debug:

        return "{}?{}".format(s, datetime.datetime.now().strftime("%s"))
    else:
        return "{}?{}".format(s, os.getenv("GIT_TAG", None) or __version__)


@app.template_filter('pluralize')
def pluralize(count, plural='s', singular=''):
    """
    Example usage:

    - Found {{pages|length}} page{{pages|length|pluralize("s")}} matching the pattern.
    - We visited {{num_countries}} countr{{ num_countries|pluralize:("ies","y") }}.
    """
    if count == 1:
        return singular
    else:
        return plural


@app.template_filter('urlquote')
def urlquote(s):
    """
    Example usage:

    {{ url_for('inline_attachment', pagepath=pagepath) | urlquote }}.
    """
    squote = s.replace("'", "%27").replace("\"", "%22")
    return squote


@app.template_filter("format_datetime")
def format_datetime(value: datetime.datetime, format="medium") -> str:
    if type(value) is not datetime.datetime:
        app.logger.warning(f"format_datetime: {value=} is not datetime.datime")
        return str(value)
    if format == "medium":
        format = "%Y-%m-%d %H:%M"
    if format == "deltanow":
        if value.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(datetime.UTC)
        td = now - value

        return otterwiki.util.strfdelta_round(td, "second")
    else:  # format == 'full':
        format = "%Y-%m-%d %H:%M:%S"

    return value.strftime(format)


@app.template_filter('slugify')
def slugify(s, keep_slashes=True):
    """
    Example usage:

    {{ pagepath | slugify(keep_slashes=True) }}.
    """

    return otterwiki.util.slugify(s, keep_slashes=keep_slashes)


app.jinja_env.globals.update(os_getenv=os.getenv)


@app.after_request
def set_security_headers(response):
    if app.config['SECURITY_HEADERS']:
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'same-origin'
    return response


from otterwiki.helper import load_custom_html


def plugin_html_head_inject(page=None):
    """Template function to inject HTML into the head section via plugins."""
    results = plugin_manager.hook.template_html_head_inject(page=page)
    return ''.join(result for result in results if result)


def plugin_html_body_inject(page=None):
    """Template function to inject HTML into the body section via plugins."""
    results = plugin_manager.hook.template_html_body_inject(page=page)
    return ''.join(result for result in results if result)


def plugin_sidebar_left_inject(page=None):
    """Template function to inject HTML into the left sidebar via plugins."""
    results = plugin_manager.hook.template_html_sidebar_left_inject(page=page)
    return ''.join(result for result in results if result)


def plugin_sidebar_right_inject(page=None):
    """Template function to inject HTML into the right sidebar via plugins."""
    results = plugin_manager.hook.template_html_sidebar_right_inject(page=page)
    return ''.join(result for result in results if result)


app.jinja_env.globals.update(
    load_custom_html=load_custom_html,
    plugin_html_head_inject=plugin_html_head_inject,
    plugin_html_body_inject=plugin_html_body_inject,
    plugin_sidebar_left_inject=plugin_sidebar_left_inject,
    plugin_sidebar_right_inject=plugin_sidebar_right_inject,
)

# 多空间运行时：WSGI 前缀中间件、请求级空间门禁、模板全局。
# 必须在 models 建表之后、views 之前导入。
import otterwiki.spaces  # pyright: ignore

# initialize git via http
import otterwiki.remote

githttpserver = otterwiki.remote.GitHttpServer(path=app.config["REPOSITORY"])

# initialize repository management stuff
import otterwiki.repomgmt

otterwiki.repomgmt.initialize_repo_management(
    default_storage  # 远程同步仅绑定默认空间仓库
)

# contains application routes,
# using side-effect of import executing the file to get
import otterwiki.views  # pyright: ignore

# register CLI commands
import otterwiki.cli  # pyright: ignore
