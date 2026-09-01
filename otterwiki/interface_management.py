#!/usr/bin/env python

"""External API integration helpers for the administrator interface."""

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from otterwiki.server import app

DASHBOARD_SUMMARY_PATH = "/idp/api/dashboard/summary"
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
    """A dashboard request failed or returned an invalid response."""


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


def dashboard_summary_url() -> str:
    base_url = str(app.config.get("APSTACK_API_BASE_URL", "")).strip()
    if not base_url:
        raise InterfaceAPIError("尚未配置外部接口 Base URL。")
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InterfaceAPIError("外部接口 Base URL 配置无效。")
    return f"{base_url.rstrip('/')}{DASHBOARD_SUMMARY_PATH}"


def fetch_dashboard_summary() -> dict[str, Any]:
    request = Request(
        dashboard_summary_url(),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        timeout = max(0.1, float(app.config.get("APSTACK_API_TIMEOUT", 10)))
    except (TypeError, ValueError):
        raise InterfaceAPIError("外部接口超时时间配置无效。")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_SIZE + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        app.logger.warning("Interface dashboard request failed: %s", error)
        raise InterfaceAPIError("暂时无法连接外部接口，请稍后重试。")

    if len(raw) > MAX_RESPONSE_SIZE:
        raise InterfaceAPIError("外部接口返回的数据过大。")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InterfaceAPIError("外部接口没有返回有效的 JSON 数据。")
    return normalise_dashboard_summary(payload)
