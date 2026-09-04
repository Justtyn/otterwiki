#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

import re
import bs4
import json
from urllib.parse import unquote


def test_editor_uses_lazy_wikilink_search(test_client, monkeypatch):
    class UnexpectedPageIndex:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "The editor must not build the full page index"
            )

    monkeypatch.setattr("otterwiki.wiki.PageIndex", UnexpectedPageIndex)

    response = test_client.get("/Example/edit")
    assert response.status_code == 200
    soup = bs4.BeautifulSoup(response.data.decode(), "html.parser")
    wikilink = soup.find(id="wikilink")
    assert wikilink is not None
    assert wikilink.name == "input"
    assert wikilink.get("list") == "wikilink-options"
    assert soup.find(id="wikilink-options").name == "datalist"
    assert "/-/api/v1/pages" in response.data.decode()


def test_wikilink_page_suggestions_are_filtered_and_limited(
    create_app, test_client
):
    storage = create_app.storage
    for filename in [
        "Guides/AlphaStart.md",
        "Guides/BetaAlpha.md",
        "Reference/Other.md",
    ]:
        storage.store(
            filename,
            f"# {filename}\n",
            author=("Test", "test@example.org"),
        )

    response = test_client.get("/-/api/v1/pages?q=alpha&limit=1")
    assert response.status_code == 200
    assert response.get_json() == {"pages": ["Guides/AlphaStart"]}

    response = test_client.get("/-/api/v1/pages?q=ALPHA&limit=30")
    assert response.status_code == 200
    assert response.get_json()["pages"] == [
        "Guides/AlphaStart",
        "Guides/BetaAlpha",
    ]


def test_wikilink_page_suggestions_require_write_permission(
    app_with_user, other_client
):
    # 已登录但无写权限的用户（WRITE_ACCESS=ADMIN 时非管理员被视图层拒绝）
    app_with_user.config["WRITE_ACCESS"] = "ADMIN"
    response = other_client.get("/-/api/v1/pages?q=home")
    assert response.status_code == 403


def test_urlquote(test_client):
    for pagename in [
        "Example",
        "Example with space",
        "ExampleWith\"Doublequote",
        "Example'SingleQuote'",
        "Example_",
        "Example-Example",
        "Example_Example",
        "🙂",
        "Example '\"🙂",
    ]:
        html = test_client.get("/{}/edit".format(pagename)).data.decode()
        soup = bs4.BeautifulSoup(html, "html.parser")
        uploadUrl_found, fetchUrl_found = False, False
        for javascript in soup.find_all(
            "script", type="text/javascript", src=""
        ):
            js = javascript.text
            if "uploadUrl" not in js:
                continue
            # check uploadUrl - now using tojson filter which produces JSON strings
            m = re.search(r'uploadUrl: ("(?:[^"\\]|\\.)*"),', js)
            assert m, f"Could not find uploadUrl in JavaScript for {pagename}"
            uploadUrl_found = True
            # parse the JSON string and unquote to check if the url matches the pagename
            uploadUrl_json = json.loads(m.group(1))
            uploadUrl_path = uploadUrl_json.rsplit('/inline_attachment', 1)[0]
            uploadUrl = unquote(uploadUrl_path.lstrip('/'))
            assert (
                uploadUrl == pagename
            ), f"uploadUrl mismatch: {uploadUrl} != {pagename}"

            # check fetchUrl preview - also using tojson filter
            m = re.search(r'fetch\(("(?:[^"\\]|\\.)*"),', js)
            assert m, f"Could not find fetch URL in JavaScript for {pagename}"
            fetchUrl_found = True
            # parse the JSON string and unquote to check if the url matches the pagename
            fetchUrl_json = json.loads(m.group(1))
            fetchUrl_path = fetchUrl_json.rsplit('/preview', 1)[0].rsplit(
                '/draft', 1
            )[0]
            fetchUrl = unquote(fetchUrl_path.lstrip('/'))
            assert (
                fetchUrl == pagename
            ), f"fetchUrl mismatch: {fetchUrl} != {pagename}"

        # make sure the right block has been found and checked
        assert uploadUrl_found
        assert fetchUrl_found
