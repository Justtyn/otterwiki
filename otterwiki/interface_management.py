#!/usr/bin/env python

"""External API integration helpers for the administrator interface."""

import http.client
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler as URLRequestRedirectHandler,
    Request,
    build_opener,
)

from otterwiki.server import app

DASHBOARD_SUMMARY_PATH = "/idp/api/dashboard/summary"
SYSTEMS_PATH = "/idp/api/systems"
APPLICATIONS_PATH = "/idp/api/applications"
SCAN_TASKS_PATH = "/idp/api/scan-tasks"
SNAPSHOTS_PATH = "/idp/api/snapshot"
APPLICATION_SNAPSHOTS_PATH = f"{SNAPSHOTS_PATH}/appList"
MODULE_SNAPSHOTS_PATH = f"{SNAPSHOTS_PATH}/moduleList"
SNAPSHOT_DIFFS_PATH = f"{SNAPSHOTS_PATH}/diffs"
ASSETS_PATH = "/idp/api/assets"
TRANSACTION_ASSET_TYPES = ("TXS", "APS")
TRANSACTION_ASSET_STATUSES = (
    "DRAFT",
    "IDENTIFIED",
    "CONFIRMED",
    "PUBLISHED",
    "DEPRECATED",
    "OFFLINE",
)
ASSET_RELATIONS_PATH = f"{ASSETS_PATH}/relations"
API_GATEWAY_ASSETS_PATH = f"{ASSETS_PATH}/txs"
AUDIT_LOGS_PATH = "/idp/api/audit/logs"
AUDIT_ACTION_TYPES = (
    "ASSET_CONFIRM",
    "SCAN_TASK_DELETE",
    "APPLICATION_DELETE",
    "SYSTEM_DELETE",
    "SNAPSHOT_DELETE",
)
API_GATEWAY_ASSET_STATUSES = (
    "published",
    "downline",
    "waitTest",
    "waitPublish",
)
PACKAGE_SOURCE_TYPES = ("LOCAL_FILE", "LOCAL_DIR", "MAVEN_REPO", "HTTP_URL")
MAX_RESPONSE_SIZE = 2 * 1024 * 1024
# 扫描包上传上限的兜底值，与 defaults.DEFAULT_CONFIG 保持一致
DEFAULT_SCAN_UPLOAD_MAX_SIZE = 256 * 1024 * 1024


@dataclass(frozen=True)
class InterfaceTab:
    slug: str
    label: str


INTERFACE_TABS = (
    InterfaceTab("workbench", "工作台"),
    InterfaceTab("applications", "应用管理"),
    InterfaceTab("scan-tasks", "扫描任务"),
    InterfaceTab("application-snapshots", "应用快照"),
    InterfaceTab("module-snapshots", "模块快照"),
    InterfaceTab("transaction-assets", "交易资产目录"),
    InterfaceTab("asset-relations", "资产关系分析"),
    InterfaceTab("audit-issues", "审计问题"),
    InterfaceTab("version-comparison", "版本对比"),
    InterfaceTab("api-gateway", "API网关"),
)


class InterfaceAPIError(Exception):
    """An IDP request failed or returned an invalid response."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def normalise_mutation_result(payload: Any) -> dict[str, Any]:
    """把变更类接口的返回体归一化成固定结构。

    上游响应不能原样回传：前端成功判定需要稳定的 ``code``/``msg``/``data``，
    且任意额外字段都不应进入浏览器。
    """
    if not isinstance(payload, dict):
        raise InterfaceAPIError("外部接口返回的数据格式不正确。")
    try:
        code = int(payload.get("code", 200))
    except (TypeError, ValueError):
        code = 200
    if not 100 <= code <= 599:
        code = 200
    return {
        "code": code,
        "msg": _text(payload.get("msg") or payload.get("message"), 500),
        "data": payload.get("data"),
    }


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        # 上游偶尔把总数序列化成字符串，直接返回 0 会让分页静默失效
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def normalise_dashboard_summary(payload: Any) -> dict[str, Any]:
    """Keep only the fields used by the workbench UI."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("外部接口返回的数据格式不正确。")

    activity: list[dict[str, str]] = []
    source_activity = source.get("recentActivity", [])
    if isinstance(source_activity, list):
        for item in source_activity[:20]:
            if not isinstance(item, dict):
                continue
            activity.append(
                {
                    "date": str(item.get("date") or ""),
                    "applicationName": str(item.get("applicationName") or ""),
                    "appVersion": str(item.get("appVersion") or ""),
                    "remark": str(item.get("remark") or ""),
                }
            )

    return {
        "systemCount": _count(source.get("systemCount")),
        "applicationCount": _count(source.get("applicationCount")),
        "snapshotCount": _count(source.get("snapshotCount")),
        "assetCount": _count(source.get("assetCount")),
        "recentActivity": activity,
    }


def api_url(path: str, query: dict[str, Any] | None = None) -> str:
    base_url = str(app.config.get("APSTACK_API_BASE_URL", "")).strip()
    if not base_url:
        raise InterfaceAPIError("尚未配置外部接口 Base URL。")
    try:
        parsed = urlsplit(base_url)
        netloc = parsed.netloc
    except ValueError as error:
        raise InterfaceAPIError("外部接口 Base URL 配置无效。") from error
    if parsed.scheme not in ("http", "https") or not netloc:
        raise InterfaceAPIError("外部接口 Base URL 配置无效。")
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _path_segment(value: Any) -> str:
    """URL 路径段编码。

    ``quote(safe="")`` 不编码点号，``..`` 会作为路径段原样透传给上游接口，
    因此这里额外编码 ``.``。
    """
    return quote(str(value), safe="").replace(".", "%2E")


def _decode_json(raw: bytes, invalid_message: str) -> Any:
    if len(raw) > MAX_RESPONSE_SIZE:
        raise InterfaceAPIError("外部接口返回的数据过大。")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InterfaceAPIError(invalid_message)


def request_api_json(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if json_body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")

    request = Request(
        api_url(path, query),
        data=data,
        headers=headers,
        method=method,
    )
    return _perform_api_request(request)


def _perform_api_request(request: Request) -> Any:
    try:
        timeout = max(0.1, float(app.config.get("APSTACK_API_TIMEOUT", 10)))
    except (TypeError, ValueError):
        raise InterfaceAPIError("外部接口超时时间配置无效。")
    try:
        # 不自动跟随 30x：urllib 会把 POST 降级为 GET 并丢掉请求体，
        # PUT/DELETE 则直接抛 HTTPError，两种行为都不可预期。
        with _open_no_redirect(request, timeout) as response:
            raw = response.read(MAX_RESPONSE_SIZE + 1)
    except HTTPError as error:
        if 300 <= error.code < 400:
            app.logger.warning(
                "Interface API returned a redirect (%s)", error.code
            )
            raise InterfaceAPIError(
                "外部接口返回了重定向响应，请检查接口地址配置。", 502
            )
        raw_error = error.read(MAX_RESPONSE_SIZE + 1)
        message = "外部接口请求失败。"
        try:
            error_payload = _decode_json(raw_error, message)
            if isinstance(error_payload, dict):
                upstream_message = error_payload.get(
                    "message"
                ) or error_payload.get("msg")
                if upstream_message:
                    message = str(upstream_message)[:300]
        except InterfaceAPIError:
            pass
        app.logger.warning("Interface API request failed: %s", error)
        status_code = error.code if 400 <= error.code < 500 else 502
        raise InterfaceAPIError(message, status_code)
    except (
        URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as error:
        app.logger.warning("Interface API request failed: %s", error)
        raise InterfaceAPIError("暂时无法连接外部接口，请稍后重试。")
    except RecursionError:
        # 深层嵌套 JSON 会让解码递归爆栈，不能把 500 堆栈暴露给管理员
        app.logger.warning("Interface API response is too deeply nested")
        raise InterfaceAPIError("外部接口返回的数据结构过于复杂。")

    return _decode_json(raw, "外部接口没有返回有效的 JSON 数据。")


class LimitedReader:
    """按上限读取的流包装：超限时抛 InterfaceAPIError(413)。

    用于把上传流直接转发给外部接口，避免先把整包读进内存。
    """

    def __init__(self, stream, limit: int):
        self._stream = stream
        self._limit = max(1, int(limit))
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        block = self._stream.read(size)
        if not block:
            return block
        self._read += len(block)
        if self._read > self._limit:
            raise InterfaceAPIError(
                f"扫描包超过允许上限（{self._limit // (1024 * 1024)} MiB）。",
                413,
            )
        return block


def request_api_multipart(
    path: str,
    *,
    fields: dict[str, str],
    filename: str,
    file_data: bytes | BinaryIO,
    file_size: int | None = None,
) -> Any:
    """Send one gzip file and text fields as multipart/form-data.

    ``file_data`` 可以是字节串或文件对象；文件对象会按块转发给外部接口，
    避免把上传包在内存中再复制一份（256MiB 上限下的峰值翻倍问题）。
    """

    boundary = f"----OtterWiki{secrets.token_hex(16)}"
    safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe_filename = safe_filename.replace('"', "").replace("\r", "")
    safe_filename = safe_filename.replace("\n", "") or "package.tar.gz"
    head: list[bytes] = []
    for name, value in fields.items():
        safe_name = name.replace('"', "").replace("\r", "").replace("\n", "")
        head.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{safe_name}"'
                    "\r\n\r\n"
                ).encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            )
        )
    head.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/gzip\r\n\r\n",
        )
    )
    tail = b"\r\n" + f"--{boundary}--\r\n".encode()
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if isinstance(file_data, (bytes, bytearray)):
        body = b"".join(head) + bytes(file_data) + tail
        return _perform_api_request(
            Request(api_url(path), data=body, headers=headers, method="POST")
        )

    def chunks():
        for chunk in head:
            yield chunk
        while True:
            block = file_data.read(1024 * 1024)
            if not block:
                break
            yield block
        yield tail

    size = file_size
    if size is None:
        try:
            file_data.seek(0, os.SEEK_END)
            size = file_data.tell()
            file_data.seek(0)
        except (OSError, ValueError, AttributeError):
            size = None
    if size is not None:
        headers["Content-Length"] = str(
            sum(len(chunk) for chunk in head) + size + len(tail)
        )
    return _perform_api_request(
        Request(api_url(path), data=chunks(), headers=headers, method="POST")
    )


def fetch_dashboard_summary() -> dict[str, Any]:
    return normalise_dashboard_summary(
        request_api_json("GET", DASHBOARD_SUMMARY_PATH)
    )


def _text(value: Any, max_length: int = 500) -> str:
    if value is None or value is False:
        return ""
    if value is True:
        return "true"
    return str(value)[:max_length]


def _local_datetime(value: Any) -> str:
    text = _text(value, 100).strip()
    if not text:
        return ""
    if len(text) == 16 and text[10] == "T" and text[13] == ":":
        return text + ":00"
    return text


def _first_value(containers, keys, max_length=500):
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return _text(value, max_length)
    return ""


def normalise_system_page(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("系统列表数据格式不正确。")

    records: list[dict[str, str]] = []
    source_records = source.get("records", [])
    if isinstance(source_records, list):
        for item in source_records:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "systemId": _text(
                        item.get("systemId") or item.get("id"), 200
                    ),
                    "systemCode": _text(item.get("systemCode")),
                    "systemName": _text(item.get("systemName")),
                    "ownerDept": _text(item.get("ownerDept")),
                    "status": _text(item.get("status"), 30),
                    "remark": _text(item.get("remark"), 2000),
                }
            )

    return {
        "records": records,
        "total": _count(source.get("total")),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_systems(page_no: int, page_size: int) -> dict[str, Any]:
    payload = request_api_json(
        "GET",
        SYSTEMS_PATH,
        query={"pageNo": page_no, "pageSize": page_size},
    )
    return normalise_system_page(payload)


def validate_system_payload(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise InterfaceAPIError("请求数据格式不正确。", 400)

    system_code = _text(payload.get("systemCode"), 200).strip()
    system_name = _text(payload.get("systemName"), 300).strip()
    if not system_code:
        raise InterfaceAPIError("请填写系统编码。", 400)
    if not system_name:
        raise InterfaceAPIError("请填写系统名称。", 400)

    status = _text(payload.get("status"), 30).strip() or "ACTIVE"
    if status not in ("ACTIVE", "OFFLINE"):
        raise InterfaceAPIError("系统状态无效。", 400)

    return {
        "systemCode": system_code,
        "systemName": system_name,
        "ownerDept": _text(payload.get("ownerDept"), 300).strip(),
        "status": status,
        "remark": _text(payload.get("remark"), 2000).strip(),
    }


def system_path(system_id: Any) -> str:
    value = _text(system_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少系统 ID。", 400)
    return f"{SYSTEMS_PATH}/{_path_segment(value)}"


def normalise_application_page(payload: Any) -> dict[str, Any]:
    """Keep only application fields used by the management UI."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("应用列表数据格式不正确。")

    records: list[dict[str, str]] = []
    source_records = source.get("records", [])
    if isinstance(source_records, list):
        for item in source_records:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "appId": _text(item.get("appId") or item.get("id"), 200),
                    "systemId": _text(item.get("systemId"), 200),
                    "systemName": _text(item.get("systemName"), 300),
                    "appCode": _text(item.get("appCode"), 200),
                    "appName": _text(item.get("appName"), 300),
                    "packageSourceType": _text(
                        item.get("packageSourceType"), 30
                    ),
                    "packageNameRule": _text(item.get("packageNameRule")),
                    "packagePath": _text(item.get("packagePath"), 1000),
                    "packageGroupId": _text(item.get("packageGroupId")),
                    "packageArtifactId": _text(item.get("packageArtifactId")),
                    "remark": _text(item.get("remark"), 2000),
                }
            )

    return {
        "records": records,
        "total": _count(source.get("total")),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_applications(page_no: int, page_size: int) -> dict[str, Any]:
    payload = request_api_json(
        "GET",
        APPLICATIONS_PATH,
        query={"pageNo": page_no, "pageSize": page_size},
    )
    return normalise_application_page(payload)


def validate_application_payload(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise InterfaceAPIError("请求数据格式不正确。", 400)

    system_id = _text(payload.get("systemId"), 200).strip()
    app_code = _text(payload.get("appCode"), 200).strip()
    app_name = _text(payload.get("appName"), 300).strip()
    if not system_id:
        raise InterfaceAPIError("请选择所属系统。", 400)
    if not app_code:
        raise InterfaceAPIError("请填写应用编码。", 400)
    if not app_name:
        raise InterfaceAPIError("请填写应用名称。", 400)

    package_source_type = _text(payload.get("packageSourceType"), 30).strip()
    if package_source_type not in PACKAGE_SOURCE_TYPES:
        raise InterfaceAPIError("包来源类型无效。", 400)

    result = {
        "systemId": system_id,
        "appCode": app_code,
        "appName": app_name,
        "packageSourceType": package_source_type,
        "packageNameRule": "",
        "packagePath": "",
        "packageGroupId": "",
        "packageArtifactId": "",
        "remark": _text(payload.get("remark"), 2000).strip(),
    }
    if package_source_type == "MAVEN_REPO":
        result["packageGroupId"] = _text(payload.get("packageGroupId")).strip()
        result["packageArtifactId"] = _text(
            payload.get("packageArtifactId")
        ).strip()
    else:
        result["packageNameRule"] = _text(
            payload.get("packageNameRule")
        ).strip()
        result["packagePath"] = _text(payload.get("packagePath"), 1000).strip()
    return result


def application_path(app_id: Any) -> str:
    value = _text(app_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少应用 ID。", 400)
    return f"{APPLICATIONS_PATH}/{_path_segment(value)}"


def normalise_scan_task_page(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("扫描任务列表数据格式不正确。")

    records: list[dict[str, str]] = []
    source_records = source.get("records", [])
    if isinstance(source_records, list):
        for item in source_records:
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "scanTaskId": _text(
                        item.get("scanTaskId") or item.get("id"), 200
                    ),
                    "appId": _text(item.get("appId"), 200),
                    "applicationName": _text(
                        item.get("applicationName") or item.get("appName"),
                        300,
                    ),
                    "status": _text(
                        item.get("status") or item.get("taskStatus"), 30
                    ),
                    "packageSourceType": _text(
                        item.get("packageSourceType"), 30
                    ),
                    "packageName": _text(item.get("packageName"), 500),
                    "packagePath": _text(
                        item.get("packagePath") or item.get("resolvedRepoUrl"),
                        1000,
                    ),
                    "resolvedVersion": _text(item.get("resolvedVersion")),
                    "operator": _text(item.get("operator"), 300),
                    "startTime": _text(item.get("startTime"), 100),
                    "endTime": _text(item.get("endTime"), 100),
                    "errorMessage": _text(item.get("errorMessage"), 2000),
                    "createdAt": _text(
                        item.get("createdAt") or item.get("createTime"), 100
                    ),
                }
            )

    return {
        "records": records,
        "total": _count(source.get("total")),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def normalise_application_snapshot_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the application snapshot list."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("应用快照列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "appId": _text(item.get("appId"), 200),
                "applicationName": _text(
                    item.get("applicationName") or item.get("appName"), 300
                ),
                "appVersion": _text(item.get("appVersion")),
                "revisionNo": _text(item.get("revisionNo"), 100),
                "packageName": _text(
                    item.get("packageName") or item.get("uploadPackageName"),
                    500,
                ),
                "packageHash": _text(
                    item.get("packageHash") or item.get("uploadPackageHash"),
                    500,
                ),
                "assetHash": _text(item.get("assetHash"), 500),
                "snapshotId": _text(
                    item.get("snapshotId")
                    or item.get("appSnapshotId")
                    or item.get("id"),
                    200,
                ),
                "previousSnapshotId": _text(
                    item.get("previousSnapshotId")
                    or item.get("prevSnapshotId"),
                    200,
                ),
                "createdAt": _text(
                    item.get("createdAt") or item.get("createTime"), 100
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_application_snapshots(
    page_no: int,
    page_size: int,
    app_id: str = "",
    app_version: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    app_id = _text(app_id, 200).strip()
    app_version = _text(app_version).strip()
    if app_id:
        query["appId"] = app_id
    if app_version:
        query["appVersion"] = app_version
    payload = request_api_json("GET", APPLICATION_SNAPSHOTS_PATH, query=query)
    return normalise_application_snapshot_page(payload)


def application_snapshot_delete_path(snapshot_id: Any) -> str:
    value = _text(snapshot_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少应用快照 ID。", 400)
    return f"{SNAPSHOTS_PATH}/delete/{_path_segment(value)}"


def snapshot_options_path(app_id: Any) -> str:
    value = _text(app_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少应用 ID。", 400)
    return f"{SNAPSHOTS_PATH}/{_path_segment(value)}/snapshot-options"


def normalise_snapshot_options(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, list):
        raise InterfaceAPIError("快照选项数据格式不正确。")

    options: list[dict[str, Any]] = []
    for item in source[:1000]:
        if not isinstance(item, dict):
            continue
        snapshot_id = _text(
            item.get("snapshotId")
            or item.get("appSnapshotId")
            or item.get("id"),
            200,
        )
        if not snapshot_id:
            continue
        revision_no = item.get("revisionNo")
        label = _text(item.get("label"), 600)
        app_version = _text(item.get("appVersion"), 500)
        if not label:
            label = app_version
            if revision_no not in (None, ""):
                label = f"{label} #{_text(revision_no, 100)}".strip()
        options.append(
            {
                "snapshotId": snapshot_id,
                "appId": _text(item.get("appId"), 200),
                "appName": _text(item.get("appName"), 300),
                "appVersion": app_version,
                "revisionNo": _text(revision_no, 100),
                "label": label or snapshot_id,
                "current": bool(item.get("current")),
                "createdAt": _text(
                    item.get("createdAt") or item.get("createTime"), 100
                ),
            }
        )
    return options


def fetch_snapshot_options(app_id: Any) -> list[dict[str, Any]]:
    return normalise_snapshot_options(
        request_api_json("GET", snapshot_options_path(app_id))
    )


def _diff_side_source(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"value": value}
        if isinstance(parsed, dict):
            return parsed
    return {"value": _text(value, 4000)}


def _diff_flag(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _text(value, 30).strip()
    lowered = text.lower()
    if lowered in ("true", "yes", "y", "1"):
        return True
    if lowered in ("false", "no", "n", "0"):
        return False
    return text


def _normalise_diff_side(value: Any, dimension: str) -> dict[str, Any] | None:
    source = _diff_side_source(value)
    if source is None:
        return None
    if dimension == "TRADE_API_DEF":
        return {
            "interfaceName": _text(
                source.get("interfaceName")
                or source.get("apiName")
                or source.get("assetName")
                or source.get("name"),
                500,
            ),
            "interfaceType": _text(
                source.get("interfaceType")
                or source.get("apiType")
                or source.get("apiKind")
                or source.get("assetType")
                or source.get("type"),
                200,
            ),
            "xmlPath": _text(
                source.get("xmlPath")
                or source.get("sourceFile")
                or source.get("sourceFilePath")
                or source.get("sourcePath")
                or source.get("sourceXpath")
                or source.get("path"),
                2000,
            ),
        }
    if dimension == "TRADE_API_FIELD":
        return {
            "fieldName": _text(
                source.get("fieldName")
                or source.get("name")
                or source.get("fieldCode"),
                500,
            ),
            "fieldType": _text(
                source.get("fieldType")
                or source.get("dataType")
                or source.get("type"),
                1000,
            ),
            "required": _diff_flag(
                source.get("required")
                if source.get("required") is not None
                else (
                    source.get("isRequired")
                    if source.get("isRequired") is not None
                    else source.get("requiredFlag")
                )
            ),
            "multiple": _diff_flag(
                source.get("multiple")
                if source.get("multiple") is not None
                else (
                    source.get("isMultiple")
                    if source.get("isMultiple") is not None
                    else source.get("multiFlag")
                )
            ),
            "array": _diff_flag(
                source.get("array")
                if source.get("array") is not None
                else (
                    source.get("isArray")
                    if source.get("isArray") is not None
                    else source.get("arrayFlag")
                )
            ),
        }
    return {"value": _relation_json_text(source)}


def normalise_snapshot_diff(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("版本差异数据格式不正确。")
    summary = source.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    source_changes = source.get("changes")
    if not isinstance(source_changes, list):
        source_changes = []

    changes: list[dict[str, Any]] = []
    for item in source_changes[:5000]:
        if not isinstance(item, dict):
            continue
        dimension = _text(item.get("dimension"), 100).strip().upper()
        changes.append(
            {
                "changeType": _text(item.get("changeType"), 50),
                "dimension": dimension,
                "key": _text(item.get("key"), 1000),
                "changeDescription": _text(
                    item.get("changeDescription") or item.get("description"),
                    2000,
                ),
                "before": _normalise_diff_side(item.get("before"), dimension),
                "after": _normalise_diff_side(item.get("after"), dimension),
            }
        )

    return {
        "leftSnapshotId": _text(source.get("leftSnapshotId"), 200),
        "rightSnapshotId": _text(source.get("rightSnapshotId"), 200),
        "leftVersion": _text(source.get("leftVersion"), 500),
        "rightVersion": _text(source.get("rightVersion"), 500),
        "summary": {
            "tradeApiDefChangeCount": _count(
                summary.get("tradeApiDefChangeCount")
            ),
            "tradeApiFieldChangeCount": _count(
                summary.get("tradeApiFieldChangeCount")
            ),
        },
        "changes": changes,
    }


def fetch_snapshot_diff(
    left_snapshot_id: Any, right_snapshot_id: Any
) -> dict[str, Any]:
    left_value = _text(left_snapshot_id, 200).strip()
    right_value = _text(right_snapshot_id, 200).strip()
    if not left_value or not right_value:
        raise InterfaceAPIError("请选择左右两个快照。", 400)
    if left_value == right_value:
        raise InterfaceAPIError("左右快照不能相同。", 400)
    return normalise_snapshot_diff(
        request_api_json(
            "GET",
            SNAPSHOT_DIFFS_PATH,
            query={
                "leftSnapshotId": left_value,
                "rightSnapshotId": right_value,
            },
        )
    )


def normalise_module_snapshot_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the module snapshot list."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("模块快照列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "appId": _text(item.get("appId"), 200),
                "appSnapshotId": _text(
                    item.get("appSnapshotId")
                    or item.get("applicationSnapshotId"),
                    200,
                ),
                "moduleSnapshotId": _text(
                    item.get("moduleSnapshotId")
                    or item.get("snapshotId")
                    or item.get("id"),
                    200,
                ),
                "applicationName": _text(
                    item.get("applicationName") or item.get("appName"), 300
                ),
                "artifactId": _text(item.get("artifactId"), 500),
                "groupId": _text(item.get("groupId"), 500),
                "jarName": _text(
                    item.get("jarName")
                    or item.get("packageName")
                    or item.get("moduleName"),
                    500,
                ),
                "jarPath": _text(
                    item.get("jarPath") or item.get("modulePath"), 2000
                ),
                "createdAt": _text(
                    item.get("createdAt") or item.get("createTime"), 100
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_module_snapshots(
    page_no: int,
    page_size: int,
    app_id: str = "",
    app_snapshot_id: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    app_id = _text(app_id, 200).strip()
    app_snapshot_id = _text(app_snapshot_id, 200).strip()
    if app_id:
        query["appId"] = app_id
    if app_snapshot_id:
        query["appSnapshotId"] = app_snapshot_id
    payload = request_api_json("GET", MODULE_SNAPSHOTS_PATH, query=query)
    return normalise_module_snapshot_page(payload)


def _relation_json_text(value: Any) -> str:
    """把关联属性渲染成可读文本；列表页不再直接显示原始 JSON。"""
    if value is None:
        return ""
    parsed: Any = value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return value[:4000]
        else:
            return value[:4000]
    if isinstance(parsed, dict):
        return "；".join(
            f"{_text(key, 100)}={_text(item, 200)}"
            for key, item in list(parsed.items())[:50]
            if item not in (None, "")
        )[:4000]
    if isinstance(parsed, list):
        return "；".join(
            _text(item, 200) for item in parsed[:50] if item not in (None, "")
        )[:4000]
    return _text(parsed, 4000)


def normalise_asset_relation_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the asset relation list."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("资产关系列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        relation = item.get("relation")
        record = relation if isinstance(relation, dict) else item
        src_asset = item.get("srcAsset")
        src_asset = src_asset if isinstance(src_asset, dict) else None
        target_asset = item.get("targetAsset")
        target_asset = target_asset if isinstance(target_asset, dict) else None
        sequence = record.get("seqNo")
        if sequence is None:
            sequence = record.get("sequenceNo")
        if sequence is None:
            sequence = item.get("seqNo")
        records.append(
            {
                "relType": _text(
                    record.get("relType") or record.get("relationType"), 200
                ),
                "srcAssetCode": _first_value(
                    [item, src_asset, record],
                    (
                        "srcAssetCode",
                        "sourceAssetCode",
                        "srcAssetId",
                        "assetCode",
                    ),
                ),
                "targetAssetCode": _first_value(
                    [target_asset, item, record],
                    (
                        "targetAssetCode",
                        "targetCode",
                        "targetAssetId",
                    ),
                ),
                "seqNo": _text(sequence if sequence is not None else "", 100),
                "matchRule": _text(
                    record.get("matchRule") or record.get("matchRules"),
                    1000,
                ),
                "relAttrsJson": _relation_json_text(
                    record.get("relAttrsJson")
                    if record.get("relAttrsJson") is not None
                    else record.get("relationAttributes")
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_asset_relations(
    page_no: int, page_size: int, asset_code: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    asset_code = _text(asset_code, 500).strip()
    if asset_code:
        query["assetCode"] = asset_code
    payload = request_api_json("GET", ASSET_RELATIONS_PATH, query=query)
    return normalise_asset_relation_page(payload)


def normalise_transaction_asset_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the transaction asset catalogue."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("交易资产列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "assetId": _text(item.get("assetId") or item.get("id"), 200),
                "assetCode": _text(
                    item.get("assetCode") or item.get("asset_code"), 500
                ),
                "assetName": _text(
                    item.get("assetName") or item.get("asset_name"), 500
                ),
                "assetType": _text(item.get("assetType"), 30),
                "moduleSnapshotId": _text(item.get("moduleSnapshotId"), 200),
                "appVersion": _text(item.get("appVersion"), 500),
                "revisionNo": _text(item.get("revisionNo"), 100),
                "status": _text(
                    item.get("status") or item.get("lifecycleStatus"), 50
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_transaction_assets(
    page_no: int,
    page_size: int,
    system_id: str = "",
    app_id: str = "",
    app_snapshot_id: str = "",
    module_snapshot_id: str = "",
    asset_type: str = "",
    status: str = "",
    exposed: str = "",
    app_version: str = "",
    revision_no: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    filters = {
        "systemId": _text(system_id, 200).strip(),
        "appId": _text(app_id, 200).strip(),
        "appSnapshotId": _text(app_snapshot_id, 200).strip(),
        "moduleSnapshotId": _text(module_snapshot_id, 200).strip(),
        "assetType": _text(asset_type, 30).strip(),
        "status": _text(status, 50).strip(),
        "exposed": _text(exposed, 10).strip().lower(),
        "appVersion": _text(app_version, 500).strip(),
        "revisionNo": _text(revision_no, 100).strip(),
        "keyword": _text(keyword, 500).strip(),
    }
    if (
        filters["assetType"]
        and filters["assetType"] not in TRANSACTION_ASSET_TYPES
    ):
        raise InterfaceAPIError("资产类型筛选值无效。", 400)
    if (
        filters["status"]
        and filters["status"] not in TRANSACTION_ASSET_STATUSES
    ):
        raise InterfaceAPIError("资产状态筛选值无效。", 400)
    if filters["exposed"] and filters["exposed"] not in ("true", "false"):
        raise InterfaceAPIError("对外暴露筛选值无效。", 400)
    query.update({key: value for key, value in filters.items() if value})
    payload = request_api_json("GET", ASSETS_PATH, query=query)
    return normalise_transaction_asset_page(payload)


def transaction_asset_path(asset_id: Any) -> str:
    value = _text(asset_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少交易资产 ID。", 400)
    return f"{ASSETS_PATH}/{_path_segment(value)}"


def _normalise_asset_fields(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    fields: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        fields.append(
            {
                "fieldCode": _text(
                    item.get("fieldCode")
                    or item.get("code")
                    or item.get("name"),
                    500,
                ),
                "fieldName": _text(
                    item.get("fieldName")
                    or item.get("displayName")
                    or item.get("description")
                    or item.get("label"),
                    500,
                ),
                "fieldType": _text(
                    item.get("fieldType")
                    or item.get("dataType")
                    or item.get("type"),
                    1000,
                ),
            }
        )
    return fields


def _asset_field_group(
    source: dict[str, Any],
    basic: dict[str, Any],
    keys: tuple[str, ...],
    kinds: tuple[str, ...],
) -> list[dict[str, str]]:
    accepted = {kind.upper() for kind in kinds}

    # 1. named field lists (legacy/alternate contract)
    for container in (source, basic):
        for key in keys:
            value = container.get(key)
            if isinstance(value, list):
                return _normalise_asset_fields(value)

    # 2. flat field records carrying a scope/direction discriminator
    field_records = source.get("fieldRecords")
    if not isinstance(field_records, list):
        field_records = basic.get("fieldRecords")
    if isinstance(field_records, list):
        scoped = [
            item
            for item in field_records
            if isinstance(item, dict)
            and _text(item.get("fieldScope") or item.get("scope"), 50).upper()
            in accepted
        ]
        if scoped:
            return _normalise_asset_fields(scoped)

    # 3. grouped map keyed by scope (backend: fields -> {input/output/property})
    grouped = source.get("fields")
    if not isinstance(grouped, dict):
        grouped = basic.get("fields")
    if isinstance(grouped, dict):
        for key in keys:
            value = grouped.get(key)
            if isinstance(value, list):
                return _normalise_asset_fields(value)
        for kind in kinds:
            value = grouped.get(kind.lower())
            if isinstance(value, list):
                return _normalise_asset_fields(value)

    # 4. flat list with a direction/fieldKind/category discriminator
    all_fields = source.get("fields")
    if not isinstance(all_fields, list):
        all_fields = basic.get("fields")
    if not isinstance(all_fields, list):
        return []
    return _normalise_asset_fields(
        [
            item
            for item in all_fields
            if isinstance(item, dict)
            and _text(
                item.get("direction")
                or item.get("fieldKind")
                or item.get("category"),
                50,
            ).upper()
            in accepted
        ]
    )


def normalise_transaction_asset_detail(payload: Any) -> dict[str, Any]:
    """Normalise a transaction asset and its three interface-field groups."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("交易资产详情数据格式不正确。")
    nested = source.get("basicInfo") or source.get("asset")
    basic = nested if isinstance(nested, dict) else source
    application = source.get("application")
    application = application if isinstance(application, dict) else {}
    app_snapshot = source.get("appSnapshot")
    app_snapshot = app_snapshot if isinstance(app_snapshot, dict) else {}

    return {
        "assetId": _text(basic.get("assetId") or basic.get("id"), 200),
        "assetCode": _text(
            basic.get("assetCode") or basic.get("asset_code"), 500
        ),
        "assetName": _text(
            basic.get("assetName") or basic.get("asset_name"), 500
        ),
        "assetType": _text(basic.get("assetType"), 30),
        "moduleSnapshotId": _text(basic.get("moduleSnapshotId"), 200),
        "applicationName": _text(
            basic.get("applicationName")
            or basic.get("appName")
            or application.get("appName")
            or application.get("appCode"),
            500,
        ),
        "appVersion": _text(
            basic.get("appVersion") or app_snapshot.get("appVersion"), 500
        ),
        "revisionNo": _text(
            basic.get("revisionNo") or app_snapshot.get("revisionNo"), 100
        ),
        "sourceFile": _text(
            basic.get("sourceFile")
            or basic.get("sourceFilePath")
            or basic.get("sourcePath"),
            2000,
        ),
        "inputFields": _asset_field_group(
            source,
            basic,
            ("inputFields", "inputs", "inputParams", "requestFields"),
            ("INPUT", "IN", "REQUEST"),
        ),
        "outputFields": _asset_field_group(
            source,
            basic,
            ("outputFields", "outputs", "outputParams", "responseFields"),
            ("OUTPUT", "OUT", "RESPONSE"),
        ),
        "propertyFields": _asset_field_group(
            source,
            basic,
            ("propertyFields", "properties", "attributes", "attrs"),
            ("PROPERTY", "ATTRIBUTE", "ATTR"),
        ),
    }


def fetch_transaction_asset_detail(asset_id: Any) -> dict[str, Any]:
    return normalise_transaction_asset_detail(
        request_api_json("GET", transaction_asset_path(asset_id))
    )


def normalise_api_gateway_asset_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the API gateway asset list."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("API 网关资产列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "assetCode": _text(
                    item.get("assetCode") or item.get("asset_code"), 500
                ),
                "assetName": _text(
                    item.get("assetName") or item.get("asset_name"), 500
                ),
                "status": _text(
                    item.get("status") or item.get("lifecycleStatus"), 50
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def fetch_api_gateway_assets(
    page_no: int,
    page_size: int,
    asset_code: str = "",
    asset_name: str = "",
    status: str = "",
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    asset_code = _text(asset_code, 500).strip()
    asset_name = _text(asset_name, 500).strip()
    status = _text(status, 50).strip()
    if status and status not in API_GATEWAY_ASSET_STATUSES:
        raise InterfaceAPIError("状态筛选值无效。", 400)
    if asset_code:
        query["assetCode"] = asset_code
    if asset_name:
        query["assetName"] = asset_name
    if status:
        query["status"] = status
    payload = request_api_json("GET", API_GATEWAY_ASSETS_PATH, query=query)
    return normalise_api_gateway_asset_page(payload)


def normalise_audit_log_page(payload: Any) -> dict[str, Any]:
    """Keep only fields displayed by the audit log list."""

    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, dict):
        raise InterfaceAPIError("审计问题列表数据格式不正确。")

    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = source.get("content")
    if not isinstance(source_records, list):
        source_records = source.get("list")
    if not isinstance(source_records, list):
        source_records = []

    records: list[dict[str, str]] = []
    for item in source_records:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "logId": _text(
                    item.get("logId")
                    or item.get("auditLogId")
                    or item.get("id"),
                    200,
                ),
                "actionType": _text(item.get("actionType"), 50),
                "targetName": _text(item.get("targetName"), 500),
                "operator": _text(item.get("operator"), 300),
                "createdAt": _text(
                    item.get("createdAt") or item.get("createTime"), 100
                ),
            }
        )

    total = source.get("total")
    if total is None:
        total = source.get("totalElements")
    return {
        "records": records,
        "total": _count(total),
        "pageNo": max(1, _count(source.get("pageNo")) or 1),
        "pageSize": max(1, _count(source.get("pageSize")) or 10),
    }


def validate_audit_log_query(
    payload: Any, page_no: int, page_size: int
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InterfaceAPIError("请求数据格式不正确。", 400)

    action_type = _text(payload.get("actionType"), 50).strip()
    if action_type and action_type not in AUDIT_ACTION_TYPES:
        raise InterfaceAPIError("审计类型筛选值无效。", 400)

    query: dict[str, Any] = {
        "pageNo": min(max(page_no, 1), 100_000),
        "pageSize": min(max(page_size, 1), 100),
    }
    filters = {
        "actionType": action_type,
        "operator": _text(payload.get("operator"), 300).strip(),
        "startTime": _local_datetime(payload.get("startTime")),
        "endTime": _local_datetime(payload.get("endTime")),
    }
    query.update({key: value for key, value in filters.items() if value})
    return query


def fetch_audit_logs(
    payload: Any, page_no: int, page_size: int
) -> dict[str, Any]:
    query = validate_audit_log_query(payload, page_no, page_size)
    return normalise_audit_log_page(
        request_api_json("POST", AUDIT_LOGS_PATH, json_body={"request": query})
    )


def audit_log_path(log_id: Any) -> str:
    value = _text(log_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少审计日志 ID。", 400)
    return f"{AUDIT_LOGS_PATH}/{_path_segment(value)}"


def validate_audit_log_ids(payload: Any) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        raise InterfaceAPIError("请求数据格式不正确。", 400)
    source_ids = payload.get("logIds")
    if not isinstance(source_ids, list):
        raise InterfaceAPIError("请选择要删除的审计记录。", 400)

    log_ids: list[str] = []
    seen: set[str] = set()
    for raw_value in source_ids[:1000]:
        value = _text(raw_value, 200).strip()
        if value and value not in seen:
            seen.add(value)
            log_ids.append(value)
    if not log_ids:
        raise InterfaceAPIError("请选择要删除的审计记录。", 400)
    return {"logIds": log_ids}


def fetch_scan_tasks(
    page_no: int, page_size: int, app_id: str = ""
) -> dict[str, Any]:
    query: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
    if app_id:
        query["appId"] = _text(app_id, 200).strip()
    payload = request_api_json("GET", SCAN_TASKS_PATH, query=query)
    return normalise_scan_task_page(payload)


def validate_scan_task_payload(payload: Any, operator: str) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise InterfaceAPIError("请求数据格式不正确。", 400)
    app_id = _text(payload.get("appId"), 200).strip()
    if not app_id:
        raise InterfaceAPIError("请选择应用。", 400)
    source_type = _text(payload.get("packageSourceType"), 30).strip()
    if source_type and source_type not in PACKAGE_SOURCE_TYPES:
        raise InterfaceAPIError("部署包来源类型无效。", 400)
    selected_version = _text(payload.get("selectedVersion")).strip()
    if source_type == "MAVEN_REPO" and not selected_version:
        raise InterfaceAPIError("请选择部署包版本。", 400)
    result = {
        "appId": app_id,
        "selectedVersion": selected_version,
        "packageName": _text(payload.get("packageName"), 500).strip(),
        "operator": _text(operator, 300).strip(),
    }
    if source_type:
        result["packageSourceType"] = source_type
    return result


def scan_task_path(scan_task_id: Any, action: str = "") -> str:
    value = _text(scan_task_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少扫描任务 ID。", 400)
    encoded = _path_segment(value)
    if action == "run":
        return f"{SCAN_TASKS_PATH}/{encoded}/run"
    if action == "delete":
        return f"{SCAN_TASKS_PATH}/delete/{encoded}"
    raise InterfaceAPIError("扫描任务操作无效。", 400)


def package_versions_path(app_id: Any) -> str:
    return f"{application_path(app_id)}/package-versions"


def normalise_package_versions(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise InterfaceAPIError("外部接口返回了失败状态。")
    source = payload.get("data")
    if not isinstance(source, list):
        raise InterfaceAPIError("部署包版本数据格式不正确。")
    return [
        _text(value).strip() for value in source[:500] if _text(value).strip()
    ]


class _NoRedirectHandler(URLRequestRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_no_redirect(request: Request, timeout: float):
    opener = build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)
