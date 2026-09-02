import io
import re


def _csrf_token(client):
    html = client.get("/").data.decode()
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def test_interface_button_is_visible_to_admin(admin_client):
    admin_html = admin_client.get("/").data.decode()

    assert 'id="interface-management-btn"' in admin_html
    assert "接口管理" in admin_html

    interface_html = admin_client.get("/-/interface").data.decode()
    assert 'id="interface-management-btn"' in interface_html
    assert "返回文档" in interface_html


def test_interface_button_is_hidden_from_non_admin(other_client):
    other_html = other_client.get("/").data.decode()

    assert 'id="interface-management-btn"' not in other_html


def test_interface_pages_reject_non_admin(other_client):
    assert other_client.get("/-/interface").status_code == 403
    assert (
        other_client.get("/-/interface/api/dashboard/summary").status_code
        == 403
    )
    assert other_client.get("/-/interface/api/systems").status_code == 403
    assert other_client.get("/-/interface/api/applications").status_code == 403
    assert other_client.get("/-/interface/api/scan-tasks").status_code == 403
    assert (
        other_client.get("/-/interface/api/application-snapshots").status_code
        == 403
    )
    assert (
        other_client.get("/-/interface/api/module-snapshots").status_code
        == 403
    )
    assert (
        other_client.get("/-/interface/api/asset-relations").status_code == 403
    )
    assert (
        other_client.get("/-/interface/api/api-gateway-assets").status_code
        == 403
    )


def test_interface_page_has_all_tabs(admin_client):
    response = admin_client.get("/-/interface")
    assert response.status_code == 200
    html = response.data.decode()
    for label in (
        "工作台",
        "应用管理",
        "扫描任务",
        "应用快照",
        "模块快照",
        "交易资产目录",
        "资产关系分析",
        "审计问题",
        "版本对比",
        "API网关",
    ):
        assert label in html


def test_unknown_interface_tab_returns_not_found(admin_client):
    assert admin_client.get("/-/interface/not-a-tab").status_code == 404


def test_scan_task_page_has_table_filter_pagination_and_drawer(admin_client):
    response = admin_client.get("/-/interface/scan-tasks")

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="scan-application-filter"' in html
    assert 'id="scan-task-table-body"' in html
    assert 'id="scan-task-page-size"' in html
    assert 'id="scan-task-form"' in html
    assert "table-column-resize.js" in html
    assert 'id="scan-task-upload-fields"' in html
    assert 'id="scan-task-upload-fields" hidden' not in html
    assert 'accept=".gz,application/gzip"' in html
    assert (
        'id="scan-application-clear" class="btn" type="button" hidden>清空</button>'
        in html
    )
    assert 'id="scan-application-clear" class="btn btn-action"' not in html
    assert 'id="scan-task-page-jump-button"' not in html
    assert '<span class="page-unit">页</span>' in html
    assert "查看应用快照" not in html  # rendered safely by JavaScript


def test_application_snapshot_page_has_filters_table_and_pagination(
    admin_client,
):
    response = admin_client.get(
        "/-/interface/application-snapshots?appId=app-1&appVersion=1.2.3"
    )

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="application-snapshot-app-filter"' in html
    assert 'id="application-snapshot-version"' in html
    assert 'id="application-snapshot-compare"' in html
    assert 'id="application-snapshot-table-body"' in html
    assert 'id="application-snapshot-page-size"' in html
    assert "application-snapshot-management.js" in html
    assert "table-column-resize.js" in html
    assert 'initialAppId: "app-1"' in html
    assert 'initialAppVersion: "1.2.3"' in html
    assert html.count('<th scope="col"') == 10


def test_module_snapshot_page_has_filters_table_and_pagination(admin_client):
    response = admin_client.get(
        "/-/interface/module-snapshots?appId=app-1&snapshotId=snap-2"
    )

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="module-snapshot-app-filter"' in html
    assert 'id="module-snapshot-id"' in html
    assert 'id="module-snapshot-table-body"' in html
    assert 'id="module-snapshot-page-size"' in html
    assert "module-snapshot-management.js" in html
    assert "table-column-resize.js" in html
    assert 'initialAppId: "app-1"' in html
    assert 'initialAppSnapshotId: "snap-2"' in html
    assert html.count('<th scope="col"') == 7


def test_asset_relation_page_has_search_table_and_pagination(admin_client):
    response = admin_client.get(
        "/-/interface/asset-relations?assetCode=asset-source-1"
    )

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="asset-relation-code"' in html
    assert 'id="asset-relation-search"' in html
    assert 'id="asset-relation-table-body"' in html
    assert 'id="asset-relation-page-size"' in html
    assert "asset-relation-management.js" in html
    assert "table-column-resize.js" in html
    assert 'initialAssetCode: "asset-source-1"' in html
    assert html.count('<th scope="col"') == 6


def test_api_gateway_page_has_filters_table_and_pagination(admin_client):
    response = admin_client.get(
        "/-/interface/api-gateway"
        "?assetCode=ap0001&assetName=%E6%9F%A5%E8%AF%A2&status=published"
    )

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="api-gateway-asset-code"' in html
    assert 'id="api-gateway-asset-name"' in html
    assert 'id="api-gateway-status"' in html
    assert 'id="api-gateway-table-body"' in html
    assert 'id="api-gateway-page-size"' in html
    assert "api-gateway-management.js" in html
    assert "table-column-resize.js" in html
    assert 'initialAssetCode: "ap0001"' in html
    assert 'initialAssetName: "查询"' in html
    assert 'initialStatus: "published"' in html
    assert html.count('<th scope="col"') == 3


def test_application_management_has_two_subtabs(admin_client):
    response = admin_client.get("/-/interface/applications")
    assert response.status_code == 200
    html = response.data.decode()
    assert "系统管理" in html
    assert "应用管理" in html
    assert 'id="system-table-body"' in html
    assert "table-column-resize.js" in html
    assert 'id="system-page-jump-button"' not in html
    assert '<span class="page-unit">页</span>' in html

    application_response = admin_client.get(
        "/-/interface/applications?section=applications"
    )
    assert application_response.status_code == 200
    application_html = application_response.data.decode()
    assert 'id="application-table-body"' in application_html
    assert "table-column-resize.js" in application_html
    assert 'id="application-form"' in application_html
    assert 'id="application-page-jump-button"' not in application_html
    assert "LOCAL_FILE" in application_html
    assert "LOCAL_DIR" in application_html
    assert "MAVEN_REPO" in application_html
    assert (
        admin_client.get(
            "/-/interface/applications?section=unknown"
        ).status_code
        == 404
    )


def test_dashboard_summary_uses_idp_endpoint(create_app):
    import otterwiki.interface_management as interface_api

    create_app.config["APSTACK_API_BASE_URL"] = "http://localhost:9988/"

    assert (
        interface_api.dashboard_summary_url()
        == "http://localhost:9988/idp/api/dashboard/summary"
    )


def test_dashboard_proxy_returns_only_ui_fields(admin_client, monkeypatch):
    import otterwiki.interface_management as interface_api

    monkeypatch.setattr(
        interface_api,
        "fetch_dashboard_summary",
        lambda: {
            "systemCount": 1,
            "applicationCount": 3,
            "snapshotCount": 2,
            "assetCount": 278,
            "recentActivity": [
                {
                    "date": "2026-08-05 17:03",
                    "applicationName": "dppb-adm-onl-dist",
                    "appVersion": "8.7.0.2-RC-prog",
                    "remark": "应用扫描完成",
                }
            ],
        },
    )

    response = admin_client.get("/-/interface/api/dashboard/summary")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["data"]["assetCount"] == 278
    assert "publishedAssetCount" not in response.json["data"]


def test_system_list_proxy_forwards_pagination_and_filters_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "msg": "success",
            "data": {
                "records": [
                    {
                        "systemId": "sys-1",
                        "systemCode": "core",
                        "systemName": "核心系统",
                        "ownerDept": "研发部",
                        "status": "ACTIVE",
                        "remark": "备注",
                        "unused": "discard",
                    }
                ],
                "total": 1,
                "pageNo": 2,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/systems?pageNo=2&pageSize=20"
    )

    assert response.status_code == 200
    assert calls == [
        ("GET", "/idp/api/systems", {"pageNo": 2, "pageSize": 20}, None)
    ]
    assert response.json["data"]["records"][0] == {
        "systemId": "sys-1",
        "systemCode": "core",
        "systemName": "核心系统",
        "ownerDept": "研发部",
        "status": "ACTIVE",
        "remark": "备注",
    }


def test_system_create_proxy_validates_and_forwards_json(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {"systemId": "1"}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.open(
        "/-/interface/api/systems",
        method="POST",
        json={
            "systemCode": " core ",
            "systemName": " 核心系统 ",
            "ownerDept": " 研发部 ",
            "status": "ACTIVE",
            "remark": " test ",
            "ignored": "value",
        },
        headers={"X-CSRFToken": _csrf_token(admin_client)},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "POST",
            "/idp/api/systems",
            None,
            {
                "systemCode": "core",
                "systemName": "核心系统",
                "ownerDept": "研发部",
                "status": "ACTIVE",
                "remark": "test",
            },
        )
    ]


def test_system_update_and_delete_use_system_id(admin_client, monkeypatch):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    token = _csrf_token(admin_client)
    update_response = admin_client.open(
        "/-/interface/api/systems/system%201",
        method="PUT",
        json={"systemCode": "core", "systemName": "核心", "status": "OFFLINE"},
        headers={"X-CSRFToken": token},
    )
    delete_response = admin_client.open(
        "/-/interface/api/systems/system%201",
        method="DELETE",
        headers={"X-CSRFToken": token},
    )

    assert update_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls[0][0:2] == ("PUT", "/idp/api/systems/system%201")
    assert calls[0][3]["status"] == "OFFLINE"
    assert calls[1] == ("DELETE", "/idp/api/systems/system%201", None, None)


def test_system_payload_rejects_invalid_status(create_app):
    import otterwiki.interface_management as interface_api

    try:
        interface_api.validate_system_payload(
            {"systemCode": "core", "systemName": "核心", "status": "UNKNOWN"}
        )
    except interface_api.InterfaceAPIError as error:
        assert error.status_code == 400
        assert str(error) == "系统状态无效。"
    else:
        raise AssertionError("invalid status was accepted")


def test_application_list_proxy_forwards_pagination_and_filters_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "msg": "success",
            "data": {
                "records": [
                    {
                        "appId": "app-1",
                        "systemId": "sys-1",
                        "systemName": "核心系统",
                        "appCode": "core-app",
                        "appName": "核心应用",
                        "packageSourceType": "LOCAL_DIR",
                        "packageNameRule": "core-*.tar.gz",
                        "packagePath": "/deploy/core",
                        "packageGroupId": "",
                        "packageArtifactId": "",
                        "remark": "备注",
                        "unused": "discard",
                    }
                ],
                "total": 1,
                "pageNo": 3,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/applications?pageNo=3&pageSize=20"
    )

    assert response.status_code == 200
    assert calls == [
        ("GET", "/idp/api/applications", {"pageNo": 3, "pageSize": 20}, None)
    ]
    assert response.json["data"]["records"][0] == {
        "appId": "app-1",
        "systemId": "sys-1",
        "systemName": "核心系统",
        "appCode": "core-app",
        "appName": "核心应用",
        "packageSourceType": "LOCAL_DIR",
        "packageNameRule": "core-*.tar.gz",
        "packagePath": "/deploy/core",
        "packageGroupId": "",
        "packageArtifactId": "",
        "remark": "备注",
    }


def test_application_create_forwards_local_source_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {"appId": "1"}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.open(
        "/-/interface/api/applications",
        method="POST",
        json={
            "systemId": " sys-1 ",
            "appCode": " core-app ",
            "appName": " 核心应用 ",
            "packageSourceType": "LOCAL_DIR",
            "packageNameRule": " core-*.tar.gz ",
            "packagePath": " /deploy/core ",
            "packageGroupId": "must-clear",
            "packageArtifactId": "must-clear",
            "remark": " test ",
            "ignored": "value",
        },
        headers={"X-CSRFToken": _csrf_token(admin_client)},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "POST",
            "/idp/api/applications",
            None,
            {
                "systemId": "sys-1",
                "appCode": "core-app",
                "appName": "核心应用",
                "packageSourceType": "LOCAL_DIR",
                "packageNameRule": "core-*.tar.gz",
                "packagePath": "/deploy/core",
                "packageGroupId": "",
                "packageArtifactId": "",
                "remark": "test",
            },
        )
    ]


def test_application_update_and_delete_use_app_id(admin_client, monkeypatch):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    token = _csrf_token(admin_client)
    update_response = admin_client.open(
        "/-/interface/api/applications/app%201",
        method="PUT",
        json={
            "systemId": "sys-1",
            "appCode": "core",
            "appName": "核心",
            "packageSourceType": "MAVEN_REPO",
            "packageNameRule": "must-clear",
            "packagePath": "must-clear",
            "packageGroupId": "cn.example",
            "packageArtifactId": "core-app",
        },
        headers={"X-CSRFToken": token},
    )
    delete_response = admin_client.open(
        "/-/interface/api/applications/app%201",
        method="DELETE",
        headers={"X-CSRFToken": token},
    )

    assert update_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls[0][0:2] == ("PUT", "/idp/api/applications/app%201")
    assert calls[0][3]["packageGroupId"] == "cn.example"
    assert calls[0][3]["packageArtifactId"] == "core-app"
    assert calls[0][3]["packageNameRule"] == ""
    assert calls[0][3]["packagePath"] == ""
    assert calls[1] == (
        "DELETE",
        "/idp/api/applications/app%201",
        None,
        None,
    )


def test_application_payload_rejects_invalid_package_source(create_app):
    import otterwiki.interface_management as interface_api

    try:
        interface_api.validate_application_payload(
            {
                "systemId": "sys-1",
                "appCode": "core",
                "appName": "核心",
                "packageSourceType": "HTTP_URL",
            }
        )
    except interface_api.InterfaceAPIError as error:
        assert error.status_code == 400
        assert str(error) == "包来源类型无效。"
    else:
        raise AssertionError("invalid package source was accepted")


def test_application_snapshot_list_forwards_filters_and_normalises_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "data": {
                "records": [
                    {
                        "appId": "app-1",
                        "appName": "核心应用",
                        "appVersion": "1.2.3",
                        "revisionNo": 2,
                        "uploadPackageName": "core-1.2.3.tar.gz",
                        "uploadPackageHash": "package-hash",
                        "assetHash": "asset-hash",
                        "appSnapshotId": "snap-2",
                        "prevSnapshotId": "snap-1",
                        "createTime": "2026-09-02T10:00:00",
                        "unused": "discard",
                    }
                ],
                "total": 1,
                "pageNo": 2,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/application-snapshots"
        "?appId=app-1&appVersion=1.2.3&pageNo=2&pageSize=20"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [
        (
            "GET",
            "/idp/api/snapshot/applist",
            {
                "pageNo": 2,
                "pageSize": 20,
                "appId": "app-1",
                "appVersion": "1.2.3",
            },
            None,
        )
    ]
    assert response.json["data"]["records"][0] == {
        "appId": "app-1",
        "applicationName": "核心应用",
        "appVersion": "1.2.3",
        "revisionNo": "2",
        "packageName": "core-1.2.3.tar.gz",
        "packageHash": "package-hash",
        "assetHash": "asset-hash",
        "snapshotId": "snap-2",
        "previousSnapshotId": "snap-1",
        "createdAt": "2026-09-02T10:00:00",
    }


def test_application_snapshot_delete_uses_documented_path(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.open(
        "/-/interface/api/application-snapshots/snap%202",
        method="DELETE",
        headers={"X-CSRFToken": _csrf_token(admin_client)},
    )

    assert response.status_code == 200
    assert calls == [
        ("DELETE", "/idp/api/snapshot/delete/snap%202", None, None)
    ]


def test_module_snapshot_list_forwards_filters_and_normalises_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "data": {
                "content": [
                    {
                        "appId": "app-1",
                        "applicationSnapshotId": "snap-2",
                        "id": "module-snap-1",
                        "appName": "核心应用",
                        "artifactId": "core-api",
                        "groupId": "cn.example",
                        "packageName": "core-api-1.2.3.jar",
                        "modulePath": "/deploy/lib/core-api-1.2.3.jar",
                        "createTime": "2026-09-02T12:00:00",
                        "unused": "discard",
                    }
                ],
                "totalElements": 1,
                "pageNo": 3,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/module-snapshots"
        "?appId=app-1&appSnapshotId=snap-2&pageNo=3&pageSize=20"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [
        (
            "GET",
            "/idp/api/snapshot/moduleList",
            {
                "pageNo": 3,
                "pageSize": 20,
                "appId": "app-1",
                "appSnapshotId": "snap-2",
            },
            None,
        )
    ]
    assert response.json["data"]["records"][0] == {
        "appId": "app-1",
        "appSnapshotId": "snap-2",
        "moduleSnapshotId": "module-snap-1",
        "applicationName": "核心应用",
        "artifactId": "core-api",
        "groupId": "cn.example",
        "jarName": "core-api-1.2.3.jar",
        "jarPath": "/deploy/lib/core-api-1.2.3.jar",
        "createdAt": "2026-09-02T12:00:00",
    }


def test_asset_relation_list_forwards_filter_and_normalises_nested_relation(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "data": {
                "list": [
                    {
                        "relation": {
                            "relationType": "TXS_CALL_APS",
                            "sourceAssetCode": "DirectInnerTran",
                            "targetAssetCode": "DirectInnerAps",
                            "sequenceNo": 1,
                            "matchRules": "service_name",
                            "relationAttributes": {
                                "serviceName": "DirectInnerAps"
                            },
                            "unused": "discard",
                        }
                    }
                ],
                "totalElements": 1,
                "pageNo": 2,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/asset-relations"
        "?assetCode=DirectInnerTran&pageNo=2&pageSize=20"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [
        (
            "GET",
            "/idp/api/assets/relations",
            {
                "pageNo": 2,
                "pageSize": 20,
                "assetCode": "DirectInnerTran",
            },
            None,
        )
    ]
    assert response.json["data"]["records"][0] == {
        "relType": "TXS_CALL_APS",
        "srcAssetCode": "DirectInnerTran",
        "targetAssetCode": "DirectInnerAps",
        "seqNo": "1",
        "matchRule": "service_name",
        "relAttrsJson": '{"serviceName":"DirectInnerAps"}',
    }


def test_api_gateway_list_forwards_filters_and_normalises_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "data": {
                "content": [
                    {
                        "asset_code": "ap0001",
                        "asset_name": "交易查询",
                        "lifecycleStatus": "published",
                        "unused": "discard",
                    }
                ],
                "totalElements": 139,
                "pageNo": 2,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/api-gateway-assets"
        "?assetCode=ap0001&assetName=%E6%9F%A5%E8%AF%A2"
        "&status=published&pageNo=2&pageSize=20"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [
        (
            "GET",
            "/idp/api/assets/txs",
            {
                "pageNo": 2,
                "pageSize": 20,
                "assetCode": "ap0001",
                "assetName": "查询",
                "status": "published",
            },
            None,
        )
    ]
    assert response.json["data"] == {
        "records": [
            {
                "assetCode": "ap0001",
                "assetName": "交易查询",
                "status": "published",
            }
        ],
        "total": 139,
        "pageNo": 2,
        "pageSize": 20,
    }


def test_api_gateway_list_rejects_unknown_status(admin_client, monkeypatch):
    import otterwiki.interface_management as interface_api

    def unexpected_request(*args, **kwargs):
        raise AssertionError("invalid filters must not reach the external API")

    monkeypatch.setattr(interface_api, "request_api_json", unexpected_request)
    response = admin_client.get(
        "/-/interface/api/api-gateway-assets?status=unknown"
    )

    assert response.status_code == 400
    assert response.json == {"code": 400, "msg": "状态筛选值无效。"}


def test_scan_task_list_forwards_filter_and_normalises_fields(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {
            "code": 200,
            "data": {
                "records": [
                    {
                        "scanTaskId": "scan-1",
                        "appId": "app-1",
                        "applicationName": "核心应用",
                        "status": "SUCCESS",
                        "packageSourceType": "LOCAL_FILE",
                        "packageName": "core.tar.gz",
                        "resolvedRepoUrl": "/tmp/core.tar.gz",
                        "resolvedVersion": "1.0.0",
                        "operator": "admin",
                        "startTime": "2026-09-01T12:00:00",
                        "endTime": "2026-09-01T12:01:00",
                        "errorMessage": "",
                        "createdAt": "2026-09-01T11:59:00",
                        "unused": "discard",
                    }
                ],
                "total": 1,
                "pageNo": 2,
                "pageSize": 20,
            },
        }

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/scan-tasks?appId=app-1&pageNo=2&pageSize=20"
    )

    assert response.status_code == 200
    assert calls == [
        (
            "GET",
            "/idp/api/scan-tasks",
            {"pageNo": 2, "pageSize": 20, "appId": "app-1"},
            None,
        )
    ]
    assert response.json["data"]["records"][0] == {
        "scanTaskId": "scan-1",
        "appId": "app-1",
        "applicationName": "核心应用",
        "status": "SUCCESS",
        "packageSourceType": "LOCAL_FILE",
        "packageName": "core.tar.gz",
        "packagePath": "/tmp/core.tar.gz",
        "resolvedVersion": "1.0.0",
        "operator": "admin",
        "startTime": "2026-09-01T12:00:00",
        "endTime": "2026-09-01T12:01:00",
        "errorMessage": "",
        "createdAt": "2026-09-01T11:59:00",
    }


def test_maven_scan_task_create_injects_current_operator(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {"scanTaskId": "1"}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.open(
        "/-/interface/api/scan-tasks",
        method="POST",
        json={
            "appId": " app-1 ",
            "packageSourceType": "MAVEN_REPO",
            "selectedVersion": " 1.2.3 ",
            "packageName": " core-app ",
            "operator": "must-not-forward",
        },
        headers={"X-CSRFToken": _csrf_token(admin_client)},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "POST",
            "/idp/api/scan-tasks",
            None,
            {
                "appId": "app-1",
                "selectedVersion": "1.2.3",
                "packageName": "core-app",
                "operator": "Test User",
            },
        )
    ]


def test_scan_task_run_and_delete_use_documented_paths(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "msg": "success", "data": {}}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    token = _csrf_token(admin_client)
    run_response = admin_client.open(
        "/-/interface/api/scan-tasks/scan%201/run",
        method="POST",
        headers={"X-CSRFToken": token},
    )
    delete_response = admin_client.open(
        "/-/interface/api/scan-tasks/scan%201",
        method="DELETE",
        headers={"X-CSRFToken": token},
    )

    assert run_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls == [
        ("POST", "/idp/api/scan-tasks/scan%201/run", None, None),
        ("DELETE", "/idp/api/scan-tasks/delete/scan%201", None, None),
    ]


def test_package_versions_proxy_filters_non_string_values(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_request(method, path, *, query=None, json_body=None):
        calls.append((method, path, query, json_body))
        return {"code": 200, "data": ["1.0.0", "", None, "2.0.0"]}

    monkeypatch.setattr(interface_api, "request_api_json", fake_request)
    response = admin_client.get(
        "/-/interface/api/applications/app%201/package-versions"
    )

    assert response.status_code == 200
    assert response.json["data"] == ["1.0.0", "2.0.0"]
    assert calls == [
        (
            "GET",
            "/idp/api/applications/app%201/package-versions",
            None,
            None,
        )
    ]


def test_scan_task_upload_forwards_gzip_and_operator(
    admin_client, monkeypatch
):
    import otterwiki.interface_management as interface_api

    calls = []

    def fake_multipart(path, *, fields, filename, file_data):
        calls.append((path, fields, filename, file_data))
        return {"code": 200, "msg": "success", "data": {"scanTaskId": "1"}}

    monkeypatch.setattr(interface_api, "request_api_multipart", fake_multipart)
    response = admin_client.post(
        "/-/interface/api/scan-tasks/upload",
        data={
            "appId": "app-1",
            "packageName": "core.tar.gz",
            "file": (io.BytesIO(b"gzip-data"), "core.tar.gz"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls == [
        (
            "/idp/api/scan-tasks/upload",
            {
                "appId": "app-1",
                "operator": "Test User",
                "packageName": "core.tar.gz",
            },
            "core.tar.gz",
            b"gzip-data",
        )
    ]


def test_scan_task_upload_rejects_non_gzip(admin_client):
    response = admin_client.post(
        "/-/interface/api/scan-tasks/upload",
        data={
            "appId": "app-1",
            "file": (io.BytesIO(b"not-gzip"), "core.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.json["msg"] == "只能上传 .gz 文件。"


def test_normalise_dashboard_summary_discards_unused_fields(create_app):
    import otterwiki.interface_management as interface_api

    result = interface_api.normalise_dashboard_summary(
        {
            "code": 200,
            "data": {
                "systemCount": 1,
                "applicationCount": 3,
                "snapshotCount": 2,
                "assetCount": 278,
                "publishedAssetCount": 9,
                "recentActivity": [
                    {
                        "date": "2026-08-05 17:03",
                        "applicationName": "demo",
                        "appVersion": "1.0",
                        "remark": "快照已生成",
                        "unused": "discard me",
                    }
                ],
            },
        }
    )

    assert result == {
        "systemCount": 1,
        "applicationCount": 3,
        "snapshotCount": 2,
        "assetCount": 278,
        "recentActivity": [
            {
                "date": "2026-08-05 17:03",
                "applicationName": "demo",
                "appVersion": "1.0",
                "remark": "快照已生成",
            }
        ],
    }
