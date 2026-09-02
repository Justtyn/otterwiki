#!/usr/bin/env python

"""External API integration helpers for the administrator interface."""

import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from otterwiki.server import app

DASHBOARD_SUMMARY_PATH = "/idp/api/dashboard/summary"
SYSTEMS_PATH = "/idp/api/systems"
APPLICATIONS_PATH = "/idp/api/applications"
SCAN_TASKS_PATH = "/idp/api/scan-tasks"
SNAPSHOTS_PATH = "/idp/api/snapshot"
APPLICATION_SNAPSHOTS_PATH = f"{SNAPSHOTS_PATH}/applist"
MODULE_SNAPSHOTS_PATH = f"{SNAPSHOTS_PATH}/moduleList"
ASSETS_PATH = "/idp/api/assets"
ASSET_RELATIONS_PATH = f"{ASSETS_PATH}/relations"
API_GATEWAY_ASSETS_PATH = f"{ASSETS_PATH}/txs"
API_GATEWAY_ASSET_STATUSES = (
    "published",
    "downline",
    "waitTest",
    "waitPublish",
)
PACKAGE_SOURCE_TYPES = ("LOCAL_FILE", "LOCAL_DIR", "MAVEN_REPO")
SCAN_TASK_STATUSES = ("INIT", "RUNNING", "SUCCESS", "FAIL")
MAX_RESPONSE_SIZE = 2 * 1024 * 1024


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


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
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
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InterfaceAPIError("外部接口 Base URL 配置无效。")
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def dashboard_summary_url() -> str:
    return api_url(DASHBOARD_SUMMARY_PATH)


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
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_SIZE + 1)
    except HTTPError as error:
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
    except (URLError, TimeoutError, OSError) as error:
        app.logger.warning("Interface API request failed: %s", error)
        raise InterfaceAPIError("暂时无法连接外部接口，请稍后重试。")

    return _decode_json(raw, "外部接口没有返回有效的 JSON 数据。")


def request_api_multipart(
    path: str,
    *,
    fields: dict[str, str],
    filename: str,
    file_data: bytes,
) -> Any:
    """Send one gzip file and text fields as multipart/form-data."""

    boundary = f"----OtterWiki{secrets.token_hex(16)}"
    safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    safe_filename = safe_filename.replace('"', "").replace("\r", "")
    safe_filename = safe_filename.replace("\n", "") or "package.tar.gz"
    chunks: list[bytes] = []
    for name, value in fields.items():
        safe_name = name.replace('"', "").replace("\r", "").replace("\n", "")
        chunks.extend(
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
    chunks.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/gzip\r\n\r\n",
            file_data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    body = b"".join(chunks)
    request = Request(
        api_url(path),
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    return _perform_api_request(request)


def fetch_dashboard_summary() -> dict[str, Any]:
    return normalise_dashboard_summary(
        request_api_json("GET", DASHBOARD_SUMMARY_PATH)
    )


def _text(value: Any, max_length: int = 500) -> str:
    return str(value or "")[:max_length]


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
    return f"{SYSTEMS_PATH}/{quote(value, safe='')}"


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
    return f"{APPLICATIONS_PATH}/{quote(value, safe='')}"


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
    return f"{SNAPSHOTS_PATH}/delete/{quote(value, safe='')}"


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
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:4000]
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[
            :4000
        ]
    except (TypeError, ValueError):
        return _text(value, 4000)


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
        sequence = record.get("seqNo")
        if sequence is None:
            sequence = record.get("sequenceNo")
        records.append(
            {
                "relType": _text(
                    record.get("relType") or record.get("relationType"), 200
                ),
                "srcAssetCode": _text(
                    record.get("srcAssetCode")
                    or record.get("sourceAssetCode"),
                    500,
                ),
                "targetAssetCode": _text(
                    record.get("targetAssetCode") or record.get("targetCode"),
                    500,
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
    selected_version = _text(payload.get("selectedVersion")).strip()
    if source_type == "MAVEN_REPO" and not selected_version:
        raise InterfaceAPIError("请选择部署包版本。", 400)
    return {
        "appId": app_id,
        "selectedVersion": selected_version,
        "packageName": _text(payload.get("packageName"), 500).strip(),
        "operator": _text(operator, 300).strip(),
    }


def scan_task_path(scan_task_id: Any, action: str = "") -> str:
    value = _text(scan_task_id, 200).strip()
    if not value:
        raise InterfaceAPIError("缺少扫描任务 ID。", 400)
    encoded = quote(value, safe="")
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
