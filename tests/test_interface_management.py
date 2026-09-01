def test_interface_button_is_visible_to_admin(admin_client):
    admin_html = admin_client.get("/").data.decode()

    assert 'id="interface-management-btn"' in admin_html
    assert "接口管理" in admin_html


def test_interface_button_is_hidden_from_non_admin(other_client):
    other_html = other_client.get("/").data.decode()

    assert 'id="interface-management-btn"' not in other_html


def test_interface_pages_reject_non_admin(other_client):
    assert other_client.get("/-/interface").status_code == 403
    assert (
        other_client.get("/-/interface/api/dashboard/summary").status_code
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
