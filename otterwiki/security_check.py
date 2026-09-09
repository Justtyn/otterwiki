#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""
otterwiki.security_check

Extensible security check framework for Otterwiki.
Each check is a function registered via the @register_backend_check decorator
that returns a SecurityCheckResult (or a list of) if an issue is found, None otherwise.
"""

import os
import re
import ipaddress

from bs4 import BeautifulSoup
from flask import request
from otterwiki.server import app
from otterwiki.plugins import plugin_manager
from otterwiki.util import empty


class SecurityCheckResult:
    """Represents a single security check result.

    `passed=False` results are issues to display in the issues table
    (severity is required). `passed=True` results are reported under
    the "checks passed" table; severity is ignored there.
    """

    def __init__(self, issue, description, severity=None, passed=False):
        self.issue = issue
        self.description = description
        self.severity = severity
        self.passed = passed

    def to_dict(self):
        d = {
            "issue": self.issue,
            "description": self.description,
            "passed": self.passed,
        }
        if not self.passed:
            d["severity"] = self.severity
        return d


# registry of backend check functions
_backend_checks = []


def register_backend_check(func):
    """Decorator to register a backend security check function."""
    _backend_checks.append(func)
    return func


def run_backend_checks():
    """Run all registered backend security checks.

    Returns a dict ``{"issues": [...], "passed": [...]}`` where each
    entry is the dict produced by :py:meth:`SecurityCheckResult.to_dict`.
    """
    issues = []
    passed = []
    for check_func in _backend_checks:
        try:
            result = check_func()
            if result is None:
                continue
            results = result if isinstance(result, list) else [result]
            for r in results:
                (passed if r.passed else issues).append(r.to_dict())
        except Exception as e:
            app.logger.warning(
                f"Security check '{check_func.__name__}' failed: {e}"
            )
    return {"issues": issues, "passed": passed}


#
# helper functions
#


def _get_custom_dir():
    """Get the custom files directory path, respecting USE_STATIC_PATH env var."""
    return os.path.join(
        os.getenv(
            "USE_STATIC_PATH",
            os.path.join(app.root_path, "static"),
        ),
        "custom",
    )


def _has_css_rules(content):
    """Check if CSS content has actual rules beyond comments and whitespace."""
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = content.strip()
    return bool(content)


def _has_js_code(content):
    """Check if JS content has actual code beyond comments and whitespace."""
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"//.*$", "", content, flags=re.MULTILINE)
    content = content.strip()
    return bool(content)


def _has_html_content(content):
    """Check if HTML content has actual content beyond comments and whitespace."""
    soup = BeautifulSoup(content, "html.parser")
    return bool(soup.get_text(strip=True)) or soup.find() is not None


def _is_private_ip(ip_str):
    """Check if an IP address is private or loopback."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _is_loopback_host():
    """Return True if the request's Host header points at loopback.

    Used to downgrade severity on local/dev access; the wiki may still be
    reachable via tunnel or port forward, so the downgrade is communicated
    in the description.
    """
    try:
        host = request.host or ""
    except RuntimeError:
        return False
    # strip port
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_LOOPBACK_NOTE = (
    " <em>由于本次访问来自回环地址，严重级别已降低；"
    "若此服务器可从其他网络访问，请按严重问题处理。</em>"
)


#
# backend security checks
#


@register_backend_check
def check_anonymous_write_access():
    """Check if anonymous users can edit the wiki."""
    if app.config.get("WRITE_ACCESS", "").upper() == "ANONYMOUS":
        loopback = _is_loopback_host()
        description = (
            "匿名用户拥有你的 Wiki 的写权限，任何人都无需登录即可创建或编辑页面。"
            "如果这不是有意为之，则存在重大安全风险。"
            '可在<a href="/-/admin/permissions_and_registration">'
            "权限与注册</a>页面修改此设置。"
        )
        if loopback:
            description += _LOOPBACK_NOTE
        return SecurityCheckResult(
            issue="匿名用户可以编辑 Wiki",
            description=description,
            severity="MEDIUM" if loopback else "CRITICAL",
        )
    return SecurityCheckResult(
        issue="匿名写权限已关闭",
        description="写入内容需要先登录账户。",
        passed=True,
    )


@register_backend_check
def check_anonymous_upload_access():
    """Check if anonymous users can upload files."""
    if app.config.get("ATTACHMENT_ACCESS", "").upper() == "ANONYMOUS":
        loopback = _is_loopback_host()
        description = (
            "匿名用户拥有你的 Wiki 的附件上传权限，任何人都无需登录即可上传文件。"
            "如果这不是有意为之，则存在重大安全风险。"
            '可在<a href="/-/admin/permissions_and_registration">'
            "权限与注册</a>页面修改此设置。"
        )
        if loopback:
            description += _LOOPBACK_NOTE
        return SecurityCheckResult(
            issue="匿名用户可以上传文件",
            description=description,
            severity="MEDIUM" if loopback else "CRITICAL",
        )
    return SecurityCheckResult(
        issue="匿名上传权限已关闭",
        description="上传附件需要先登录账户。",
        passed=True,
    )


@register_backend_check
def check_server_name_not_set():
    """Check if SERVER_NAME is configured."""
    if not app.config.get("SERVER_NAME"):
        return SecurityCheckResult(
            issue="未配置站点名称",
            description=(
                "尚未设置 <code>SERVER_NAME</code> 配置项。"
                "视反向代理配置而定，这可能引发问题。"
                '详情参见 <a href="https://flask.palletsprojects.com/en/stable/config/#SERVER_NAME">'
                "Flask 文档</a>。"
                '可在<a href="/-/admin">'
                "应用偏好设置</a>页面修改此设置。"
            ),
            severity="NOTICE",
        )
    return SecurityCheckResult(
        issue="站点名称已配置",
        description="已设置 <code>SERVER_NAME</code> 配置项。",
        passed=True,
    )


@register_backend_check
def check_open_registrations():
    """Check if registrations are fully open without any restrictions."""
    if (
        not app.config.get("DISABLE_REGISTRATION", False)
        and not app.config.get("EMAIL_NEEDS_CONFIRMATION", True)
        and app.config.get("AUTO_APPROVAL", True)
    ):
        return SecurityCheckResult(
            issue="注册完全开放",
            description=(
                "你的 Wiki 允许无限注册，且无需邮箱确认或人工批准。"
                "这意味着任何人都可以不受限制地创建账户，"
                "可能被垃圾用户灌水。"
                '可在<a href="/-/admin/permissions_and_registration">'
                "权限与注册</a>页面修改此设置。"
            ),
            severity="HIGH",
        )
    return SecurityCheckResult(
        issue="注册已受限制",
        description=("新用户注册已禁用，或需要邮箱确认/人工批准。"),
        passed=True,
    )


@register_backend_check
def check_plugins_active():
    """Check if plugins are active."""
    plugin_info = plugin_manager.list_plugin_distinfo()
    if plugin_info:
        plugin_names = [dist.project_name for _, dist in plugin_info]
        return SecurityCheckResult(
            issue="插件已启用",
            description=(
                "你正在使用 Wiki 插件。请注意，插件的能力几乎不受限制，"
                "第三方插件也不会经过 OtterWiki 开发者的安全审查。"
                "当前启用的插件："
                f"<strong>{', '.join(plugin_names)}</strong>。"
            ),
            severity="NOTICE",
        )
    return None


@register_backend_check
def check_custom_css():
    """Check if custom CSS rules are defined."""
    custom_dir = _get_custom_dir()
    css_path = os.path.join(custom_dir, "custom.css")
    try:
        if os.path.exists(css_path):
            with open(css_path, "r") as f:
                content = f.read()
            if _has_css_rules(content):
                return SecurityCheckResult(
                    issue="正在使用自定义 CSS",
                    description=(
                        "你在 <code>custom.css</code> 中定义了自定义 CSS 规则。"
                        "如果这是有意为之，可忽略此提示。"
                    ),
                    severity="NOTICE",
                )
    except Exception:
        pass
    return None


@register_backend_check
def check_custom_js():
    """Check if custom JavaScript code is defined."""
    custom_dir = _get_custom_dir()
    js_path = os.path.join(custom_dir, "custom.js")
    try:
        if os.path.exists(js_path):
            with open(js_path, "r") as f:
                content = f.read()
            if _has_js_code(content):
                return SecurityCheckResult(
                    issue="正在使用自定义 JavaScript",
                    description=(
                        "你在 <code>custom.js</code> 中定义了自定义 JavaScript 代码。"
                        "如果这是有意为之，可忽略此提示。"
                    ),
                    severity="NOTICE",
                )
    except Exception:
        pass
    return None


@register_backend_check
def check_custom_html():
    """Check if custom HTML content is being used."""
    findings = []
    custom_dir = _get_custom_dir()

    head_path = os.path.join(custom_dir, "customHead.html")
    try:
        if os.path.exists(head_path):
            with open(head_path, "r") as f:
                content = f.read()
            if _has_html_content(content):
                findings.append("customHead.html")
    except Exception:
        pass

    body_path = os.path.join(custom_dir, "customBody.html")
    try:
        if os.path.exists(body_path):
            with open(body_path, "r") as f:
                content = f.read()
            if _has_html_content(content):
                findings.append("customBody.html")
    except Exception:
        pass

    if not empty(app.config.get("HTML_EXTRA_HEAD", "")):
        findings.append("HTML_EXTRA_HEAD")

    if not empty(app.config.get("HTML_EXTRA_BODY", "")):
        findings.append("HTML_EXTRA_BODY")

    if findings:
        return SecurityCheckResult(
            issue="正在使用自定义 HTML",
            description=(
                "你在以下位置定义了自定义 HTML：<strong>"
                + "、".join(findings)
                + "</strong>。如果这是有意为之，可忽略此提示。"
            ),
            severity="NOTICE",
        )
    return None


@register_backend_check
def check_html_whitelist():
    """Check if RENDERER_HTML_ALLOWLIST is configured."""
    if not empty(app.config.get("RENDERER_HTML_ALLOWLIST", "")):
        return SecurityCheckResult(
            issue="已配置自定义 HTML 白名单",
            description=(
                "<code>RENDERER_HTML_ALLOWLIST</code> 不为空，"
                "这允许在 Wiki 内容中使用可能不安全的额外 HTML 标签和属性。"
                "如果这是有意为之，可忽略此提示。"
            ),
            severity="NOTICE",
        )
    return None


@register_backend_check
def check_reverse_proxy():
    """Check for missing or misconfigured reverse proxy.

    Triggers in these cases:
    - Request from a non-private IP with no proxy headers > likely no reverse proxy at all
    - X-Forwarded-For present but X-Forwarded-Proto missing > incomplete proxy setup
    """
    has_real_ip = bool(request.headers.get("X-Real-IP"))
    has_forwarded_for = bool(request.headers.get("X-Forwarded-For"))
    has_forwarded_proto = bool(request.headers.get("X-Forwarded-Proto"))
    has_proxy_headers = has_real_ip or has_forwarded_for or has_forwarded_proto
    remote_addr = request.remote_addr
    is_private = _is_private_ip(remote_addr)

    # direct local access without any proxy headers is likely fine
    if is_private and not has_proxy_headers:
        return None

    issues = []
    issue_title = "反向代理配置有误"

    # non-private IP with no proxy headers
    if not is_private and not has_proxy_headers:
        issue_title = "缺少反向代理或配置有误"
        issues.append(
            "你的 Wiki 似乎未经过反向代理就直接暴露在互联网上，"
            "或反向代理配置有误。"
            "这种做法会让应用服务器直接对外暴露，"
            "不建议使用。"
        )

    # X-Forwarded-For present but X-Forwarded-Proto missing
    if has_forwarded_for and not has_forwarded_proto:
        issues.append(
            "请求头中存在 <code>X-Forwarded-For</code> 但缺少 "
            "<code>X-Forwarded-Proto</code>，导致 Wiki 无法"
            "判断原始协议。"
        )

    if issues:
        # HIGH severity if we suspect no reverse proxy exists at all
        suspected_no_proxy = not is_private and not has_proxy_headers
        # HIGH severity if there is a proxy but X-Forwarded-Proto is missing
        proxy_without_proto = has_proxy_headers and not has_forwarded_proto

        severity = (
            "HIGH" if (suspected_no_proxy or proxy_without_proto) else "MEDIUM"
        )

        issues_list = "".join(f"<li>{issue}</li>" for issue in issues)
        return SecurityCheckResult(
            issue=issue_title,
            description=(
                "你的 Wiki 似乎没有运行在配置正确的反向代理之后："
                f"<ul>{issues_list}</ul>"
                "请参阅文档中的"
                '<a href="https://otterwiki.com/Installation#reverse-proxy">'
                "反向代理配置示例</a>和"
                '<a href="https://otterwiki.com/Configuration#reverse-proxy-and-ips">'
                "所需的 Wiki 参数</a>。"
            ),
            severity=severity,
        )

    return SecurityCheckResult(
        issue="反向代理配置正常",
        description="代理请求头看起来一致。",
        passed=True,
    )
