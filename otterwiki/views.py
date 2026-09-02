#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

import os
from timeit import default_timer as timer

from flask import (
    request,
    send_from_directory,
    abort,
    render_template,
    make_response,
    redirect,
    url_for,
    jsonify,
    g,
)
from otterwiki.server import app, githttpserver
from otterwiki.wiki import (
    Page,
    Changelog,
    Search,
    AutoRoute,
)
from otterwiki.sitemap import sitemap as generate_sitemap
from otterwiki.pageindex import PageIndex, search_page_paths
from otterwiki.sidebar import SidebarPageIndex, SidebarMenu
import otterwiki.auth
import otterwiki.preferences
import otterwiki.navigation_editor
import otterwiki.interface_management
import otterwiki.tools
from otterwiki.renderer import render
from otterwiki.helper import (
    toast,
    health_check,
    get_pagename_prefixes,
)
from otterwiki.version import __version__
from otterwiki.util import (
    sanitize_pagename,
    compute_webhook_hash,
    compute_webhook_hash_legacy,
)
from otterwiki.plugins import call_hook, collect_hook
import otterwiki.pluginmgmt

from flask_login import current_user, login_required
from otterwiki.server import csrf
from flask_wtf.csrf import CSRFError


#
# technical views/routes/redirects
#
@app.route("/")
def index():
    home_page = app.config.get("HOME_PAGE", "")

    if not home_page:
        return view()

    # special page (starts with /-/) - redirect
    if home_page.startswith("/-/"):
        return redirect(home_page)

    return view(path=home_page)


@app.route("/robots.txt")
def robotstxt():
    if app.config["ROBOTS_TXT"] == "allow":
        txt = "User-agent: *\nAllow: /"
    elif app.config["ROBOTS_TXT"] == "disallow":
        txt = "User-agent: *\nDisallow: /"
    else:  # this a fallback, in case of a typo: disallow
        txt = "User-agent: *\nDisallow: /"
    response = make_response(
        txt,
        200,
    )
    response.mimetype = "text/plain"
    return response


@app.route("/sitemap.xml")
def sitemap():
    return generate_sitemap()


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static/img"),
        "otter-favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/manifest.webmanifest")
def webmanifest():
    return jsonify(
        {
            "name": (
                app.config['SITE_NAME']
                if app.config['SITE_NAME']
                else "An Otter Wiki"
            ),
            "icons": [
                {
                    "src": url_for(
                        "static",
                        filename="img/otterhead-up-192.png",
                        _external=True,
                    ),
                    "type": "image/png",
                    "sizes": "192x192",
                },
                {
                    "src": url_for(
                        "static",
                        filename="img/otterhead-up-512.png",
                        _external=True,
                    ),
                    "type": "image/png",
                    "sizes": "512x512",
                },
                {
                    "src": url_for(
                        "static",
                        filename="img/otterhead-up-512-maskable.png",
                        _external=True,
                    ),
                    "type": "image/png",
                    "sizes": "512x512",
                    "purpose": "maskable",
                },
            ],
        }
    )


@app.route("/.well-known/change-password")
def well_known_change_password():
    return redirect(url_for("settings"))


@app.route("/-/healthz")
def healthz():
    healthy, msgs = health_check()
    return (
        "\n".join(msgs),
        200 if healthy else 503,
        {'Content-Type': 'text/plain; charset=utf-8'},
    )


#
# wiki views
#
@app.route("/-/about")
def about():
    with open(os.path.join(app.root_path, "about.md")) as f:
        content = f.read()
    htmlcontent, _, library_requirements = render.markdown(content)
    return render_template(
        "about.html",
        title="关于",
        htmlcontent=htmlcontent,
        __version__=__version__,
        library_requirements=library_requirements,
    )


@app.route("/-/syntax")
def syntax():
    return render_template(
        "syntax.html",
        title="语法",
        in_help=True,
        pagepath="",
    )


@app.route("/-/help")
@app.route("/-/help/<string:topic>")
def help(topic=None):
    toc = None
    content = "TODO"
    library_requirements = {}
    if topic == "admin":
        with open(os.path.join(app.root_path, "help_admin.md")) as f:
            md = f.read()
            content, toc, library_requirements = render.markdown(md)
    elif topic == "syntax":
        toc = [
            (None, '', 2, s, s.lower())
            for s in [
                'Emphasis',
                'Headings',
                'Lists',
                'Links',
                'Quotes',
                'Images',
                'Tables',
                'Code',
                'Mathjax',
                'Footnotes',
                'Abbreviations',
                'Blocks',
                'Diagrams',
            ]
        ]
        # embeddings info

        embedding_info = otterwiki.pluginmgmt.collect_plugin_info(
            category="Syntax/Embeddings"
        )
        return render_template(
            "help_syntax.html",
            title="帮助 - 语法",
            toc=toc,
            in_help=True,
            embedding_info=embedding_info,
        )
    elif topic == "plugins":
        content, toc, library_requirements = (
            otterwiki.pluginmgmt.generate_help()
        )
    else:
        with open(os.path.join(app.root_path, "help.md")) as f:
            md = f.read()
            content, toc, library_requirements = render.markdown(md)
    extra_js = "".join(collect_hook("renderer_javascript"))
    if len(extra_js):
        extra_js = f"<script type=\"text/javascript\">{extra_js}</script>"
    # default help
    return render_template(
        "help.html",
        title="帮助 - {}".format(topic.capitalize()) if topic else "帮助",
        content=content,
        toc=toc,
        library_requirements=library_requirements,
        extra_js=extra_js,
    )


@app.route("/-/settings", methods=["POST", "GET"])
@login_required
def settings():
    if request.method == "GET":
        return otterwiki.auth.settings_form()
    else:
        return otterwiki.auth.handle_settings(request.form)


@app.route("/-/housekeeping", methods=["POST", "GET"])
@login_required
def housekeeping():
    if request.method == "GET":
        return otterwiki.tools.housekeeping_form()
    else:
        return otterwiki.tools.handle_housekeeping(request.form)


@app.route("/-/housekeeping/security-check", methods=["GET"])
@login_required
def security_check():
    from otterwiki.auth import has_permission
    from otterwiki.security_check import run_backend_checks

    if not has_permission("ADMIN"):
        abort(403)

    return jsonify(run_backend_checks())


@app.route(
    "/-/admin/user_management", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_user_management():
    if request.method == "GET":
        return otterwiki.preferences.user_management_form()
    else:
        return otterwiki.preferences.handle_user_management(request.form)


@app.route(
    "/-/admin/sidebar_preferences", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_sidebar_preferences():
    if request.method == "GET":
        return otterwiki.preferences.sidebar_preferences_form()
    else:
        return otterwiki.preferences.handle_sidebar_preferences(request.form)


@app.route(
    "/-/admin/permissions_and_registration", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_permissions_and_registration():
    if request.method == "GET":
        return otterwiki.preferences.permissions_and_registration_form()
    else:
        return otterwiki.preferences.handle_permissions_and_registration(
            request.form
        )


@app.route(
    "/-/admin/content_and_editing", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_content_and_editing():
    if request.method == "GET":
        return otterwiki.preferences.content_and_editing_form()
    else:
        return otterwiki.preferences.handle_content_and_editing(request.form)


@app.route(
    "/-/admin/repository_management", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_repository_management():
    if request.method == "GET":
        return otterwiki.preferences.repository_management_form()
    else:
        return otterwiki.preferences.handle_repository_management(request.form)


@app.route(
    "/-/admin/document_import", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_document_import():
    from otterwiki.document_import import (
        document_import_form,
        handle_document_import,
    )

    if request.method == "GET":
        return document_import_form()
    return handle_document_import(request.form, request.files)


@app.route(
    "/-/admin/navigation", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_navigation():
    namespace = request.values.get("namespace", "")
    section = request.values.get("section", "components")
    if request.method == "GET":
        return otterwiki.navigation_editor.navigation_editor_form(
            namespace, section
        )
    try:
        return otterwiki.navigation_editor.save_navigation_editor(request.form)
    except otterwiki.navigation_editor.NavigationEditorError as error:
        toast(str(error), "error")
        return (
            otterwiki.navigation_editor.navigation_editor_form(
                namespace, section
            ),
            400,
        )


@app.route(
    "/-/admin/mail_preferences", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin_mail_preferences():
    if request.method == "GET":
        return otterwiki.preferences.mail_preferences_form()
    else:
        return otterwiki.preferences.handle_mail_preferences(request.form)


@app.route(
    "/-/admin", methods=["POST", "GET"]
)  # pyright: ignore -- false positive
@login_required
def admin():
    if request.method == "GET":
        return otterwiki.preferences.admin_form()
    else:
        return otterwiki.preferences.handle_preferences(request.form)


@app.route("/-/interface")
@app.route("/-/interface/<string:tab>")
@login_required
def interface_management(tab="workbench"):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)

    tabs = otterwiki.interface_management.INTERFACE_TABS
    selected = next((item for item in tabs if item.slug == tab), None)
    if selected is None:
        abort(404)
    application_section = None
    if tab == "applications":
        application_section = request.args.get("section", "systems")
        if application_section not in ("systems", "applications"):
            abort(404)
    snapshot_app_id = ""
    snapshot_app_version = ""
    module_app_id = ""
    module_app_snapshot_id = ""
    asset_relation_code = ""
    gateway_asset_code = ""
    gateway_asset_name = ""
    gateway_asset_status = ""
    if tab == "application-snapshots":
        snapshot_app_id = str(request.args.get("appId", "")).strip()[:200]
        snapshot_app_version = str(request.args.get("appVersion", "")).strip()[
            :500
        ]
    elif tab == "module-snapshots":
        module_app_id = str(request.args.get("appId", "")).strip()[:200]
        module_app_snapshot_id = str(
            request.args.get("appSnapshotId")
            or request.args.get("snapshotId", "")
        ).strip()[:200]
    elif tab == "asset-relations":
        asset_relation_code = str(request.args.get("assetCode", "")).strip()[
            :500
        ]
    elif tab == "api-gateway":
        gateway_asset_code = str(request.args.get("assetCode", "")).strip()[
            :500
        ]
        gateway_asset_name = str(request.args.get("assetName", "")).strip()[
            :500
        ]
        status = str(request.args.get("status", "")).strip()[:50]
        if status in otterwiki.interface_management.API_GATEWAY_ASSET_STATUSES:
            gateway_asset_status = status
    return render_template(
        "interface_management.html",
        title=f"接口管理 - {selected.label}",
        interface_tabs=tabs,
        active_tab=selected,
        application_section=application_section,
        snapshot_app_id=snapshot_app_id,
        snapshot_app_version=snapshot_app_version,
        module_app_id=module_app_id,
        module_app_snapshot_id=module_app_snapshot_id,
        asset_relation_code=asset_relation_code,
        gateway_asset_code=gateway_asset_code,
        gateway_asset_name=gateway_asset_name,
        gateway_asset_status=gateway_asset_status,
    )


@app.route("/-/interface/api/dashboard/summary")
@login_required
def interface_dashboard_summary():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        data = otterwiki.interface_management.fetch_dashboard_summary()
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )
    response = jsonify({"code": 200, "msg": "成功", "data": data})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/-/interface/api/systems", methods=["GET", "POST"])
@login_required
def interface_systems():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        if request.method == "GET":
            page_no = request.args.get("pageNo", 1, type=int) or 1
            page_size = request.args.get("pageSize", 10, type=int) or 10
            page_no = min(max(page_no, 1), 100_000)
            page_size = min(max(page_size, 1), 100)
            data = otterwiki.interface_management.fetch_systems(
                page_no, page_size
            )
            response = jsonify({"code": 200, "msg": "成功", "data": data})
            response.headers["Cache-Control"] = "no-store"
            return response

        payload = otterwiki.interface_management.validate_system_payload(
            request.get_json(silent=True)
        )
        result = otterwiki.interface_management.request_api_json(
            "POST",
            otterwiki.interface_management.SYSTEMS_PATH,
            json_body=payload,
        )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/systems/<path:system_id>", methods=["PUT", "DELETE"]
)
@login_required
def interface_system(system_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        path = otterwiki.interface_management.system_path(system_id)
        if request.method == "PUT":
            payload = otterwiki.interface_management.validate_system_payload(
                request.get_json(silent=True)
            )
            result = otterwiki.interface_management.request_api_json(
                "PUT", path, json_body=payload
            )
        else:
            result = otterwiki.interface_management.request_api_json(
                "DELETE", path
            )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/applications", methods=["GET", "POST"])
@login_required
def interface_applications():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        if request.method == "GET":
            page_no = request.args.get("pageNo", 1, type=int) or 1
            page_size = request.args.get("pageSize", 10, type=int) or 10
            page_no = min(max(page_no, 1), 100_000)
            page_size = min(max(page_size, 1), 100)
            data = otterwiki.interface_management.fetch_applications(
                page_no, page_size
            )
            response = jsonify({"code": 200, "msg": "成功", "data": data})
            response.headers["Cache-Control"] = "no-store"
            return response

        payload = otterwiki.interface_management.validate_application_payload(
            request.get_json(silent=True)
        )
        result = otterwiki.interface_management.request_api_json(
            "POST",
            otterwiki.interface_management.APPLICATIONS_PATH,
            json_body=payload,
        )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/applications/<path:app_id>",
    methods=["PUT", "DELETE"],
)
@login_required
def interface_application(app_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        path = otterwiki.interface_management.application_path(app_id)
        if request.method == "PUT":
            payload = (
                otterwiki.interface_management.validate_application_payload(
                    request.get_json(silent=True)
                )
            )
            result = otterwiki.interface_management.request_api_json(
                "PUT", path, json_body=payload
            )
        else:
            result = otterwiki.interface_management.request_api_json(
                "DELETE", path
            )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/scan-tasks", methods=["GET", "POST"])
@login_required
def interface_scan_tasks():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        if request.method == "GET":
            page_no = request.args.get("pageNo", 1, type=int) or 1
            page_size = request.args.get("pageSize", 10, type=int) or 10
            page_no = min(max(page_no, 1), 100_000)
            page_size = min(max(page_size, 1), 100)
            data = otterwiki.interface_management.fetch_scan_tasks(
                page_no,
                page_size,
                request.args.get("appId", ""),
            )
            response = jsonify({"code": 200, "msg": "成功", "data": data})
            response.headers["Cache-Control"] = "no-store"
            return response

        payload = otterwiki.interface_management.validate_scan_task_payload(
            request.get_json(silent=True), current_user.name or ""
        )
        result = otterwiki.interface_management.request_api_json(
            "POST",
            otterwiki.interface_management.SCAN_TASKS_PATH,
            json_body=payload,
        )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/scan-tasks/upload", methods=["POST"])
@login_required
def interface_scan_task_upload():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        app_id = str(request.form.get("appId", "")).strip()[:200]
        if not app_id:
            raise otterwiki.interface_management.InterfaceAPIError(
                "请选择应用。", 400
            )
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise otterwiki.interface_management.InterfaceAPIError(
                "请选择待扫描的 .gz 文件。", 400
            )
        if not upload.filename.lower().endswith(".gz"):
            raise otterwiki.interface_management.InterfaceAPIError(
                "只能上传 .gz 文件。", 400
            )
        try:
            max_size = int(
                app.config.get(
                    "APSTACK_SCAN_UPLOAD_MAX_SIZE", 256 * 1024 * 1024
                )
            )
        except (TypeError, ValueError):
            raise otterwiki.interface_management.InterfaceAPIError(
                "扫描包上传大小配置无效。"
            )
        file_data = upload.stream.read(max_size + 1)
        if len(file_data) > max_size:
            raise otterwiki.interface_management.InterfaceAPIError(
                f"扫描包超过允许上限（{max_size // (1024 * 1024)} MiB）。",
                413,
            )
        result = otterwiki.interface_management.request_api_multipart(
            f"{otterwiki.interface_management.SCAN_TASKS_PATH}/upload",
            fields={
                "appId": app_id,
                "operator": str(current_user.name or "")[:300],
                "packageName": str(
                    request.form.get("packageName", "")
                ).strip()[:500],
            },
            filename=upload.filename,
            file_data=file_data,
        )
        return jsonify(result)
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/scan-tasks/<path:scan_task_id>/run",
    methods=["POST"],
)
@login_required
def interface_scan_task_run(scan_task_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        path = otterwiki.interface_management.scan_task_path(
            scan_task_id, "run"
        )
        return jsonify(
            otterwiki.interface_management.request_api_json("POST", path)
        )
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/scan-tasks/<path:scan_task_id>",
    methods=["DELETE"],
)
@login_required
def interface_scan_task_delete(scan_task_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        path = otterwiki.interface_management.scan_task_path(
            scan_task_id, "delete"
        )
        return jsonify(
            otterwiki.interface_management.request_api_json("DELETE", path)
        )
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/applications/<path:app_id>/package-versions",
    methods=["GET"],
)
@login_required
def interface_application_package_versions(app_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        payload = otterwiki.interface_management.request_api_json(
            "GET",
            otterwiki.interface_management.package_versions_path(app_id),
        )
        data = otterwiki.interface_management.normalise_package_versions(
            payload
        )
        response = jsonify({"code": 200, "msg": "成功", "data": data})
        response.headers["Cache-Control"] = "no-store"
        return response
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/application-snapshots", methods=["GET"])
@login_required
def interface_application_snapshots():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        page_no = request.args.get("pageNo", 1, type=int) or 1
        page_size = request.args.get("pageSize", 10, type=int) or 10
        page_no = min(max(page_no, 1), 100_000)
        page_size = min(max(page_size, 1), 100)
        data = otterwiki.interface_management.fetch_application_snapshots(
            page_no,
            page_size,
            request.args.get("appId", ""),
            request.args.get("appVersion", ""),
        )
        response = jsonify({"code": 200, "msg": "成功", "data": data})
        response.headers["Cache-Control"] = "no-store"
        return response
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route(
    "/-/interface/api/application-snapshots/<path:snapshot_id>",
    methods=["DELETE"],
)
@login_required
def interface_application_snapshot_delete(snapshot_id):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        path = otterwiki.interface_management.application_snapshot_delete_path(
            snapshot_id
        )
        return jsonify(
            otterwiki.interface_management.request_api_json("DELETE", path)
        )
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/module-snapshots", methods=["GET"])
@login_required
def interface_module_snapshots():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        page_no = request.args.get("pageNo", 1, type=int) or 1
        page_size = request.args.get("pageSize", 10, type=int) or 10
        page_no = min(max(page_no, 1), 100_000)
        page_size = min(max(page_size, 1), 100)
        data = otterwiki.interface_management.fetch_module_snapshots(
            page_no,
            page_size,
            request.args.get("appId", ""),
            request.args.get("appSnapshotId", ""),
        )
        response = jsonify({"code": 200, "msg": "成功", "data": data})
        response.headers["Cache-Control"] = "no-store"
        return response
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/asset-relations", methods=["GET"])
@login_required
def interface_asset_relations():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        page_no = request.args.get("pageNo", 1, type=int) or 1
        page_size = request.args.get("pageSize", 10, type=int) or 10
        page_no = min(max(page_no, 1), 100_000)
        page_size = min(max(page_size, 1), 100)
        data = otterwiki.interface_management.fetch_asset_relations(
            page_no,
            page_size,
            request.args.get("assetCode", ""),
        )
        response = jsonify({"code": 200, "msg": "成功", "data": data})
        response.headers["Cache-Control"] = "no-store"
        return response
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/interface/api/api-gateway-assets", methods=["GET"])
@login_required
def interface_api_gateway_assets():
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    try:
        page_no = request.args.get("pageNo", 1, type=int) or 1
        page_size = request.args.get("pageSize", 10, type=int) or 10
        page_no = min(max(page_no, 1), 100_000)
        page_size = min(max(page_size, 1), 100)
        data = otterwiki.interface_management.fetch_api_gateway_assets(
            page_no,
            page_size,
            request.args.get("assetCode", ""),
            request.args.get("assetName", ""),
            request.args.get("status", ""),
        )
        response = jsonify({"code": 200, "msg": "成功", "data": data})
        response.headers["Cache-Control"] = "no-store"
        return response
    except otterwiki.interface_management.InterfaceAPIError as error:
        return (
            jsonify({"code": error.status_code, "msg": str(error)}),
            error.status_code,
        )


@app.route("/-/user/", methods=["POST", "GET"])
@app.route("/-/user/<string:uid>", methods=["POST", "GET"])
@login_required
def user(uid=None):
    if request.method == "GET":
        return otterwiki.preferences.user_edit_form(uid)
    else:
        return otterwiki.preferences.handle_user_edit(uid, request.form)


#
# index, changelog
#
@app.route("/-/log")
@app.route("/-/log/<string:revision>")
@app.route("/-/changelog")
@app.route("/-/changelog/<string:revision>")
def changelog(revision=None):
    chlg = Changelog(revision)
    return chlg.render()


@app.route("/-/changelog/feed.rss")
def changelog_feed_rss():
    chlg = Changelog()
    response = make_response(chlg.feed_rss())
    response.headers['Content-Type'] = 'application/rss+xml; charset=utf-8'
    return response


@app.route("/-/changelog/feed.atom")
def changelog_feed_atom():
    chlg = Changelog()
    response = make_response(chlg.feed_atom())
    response.headers['Content-Type'] = 'application/atom+xml; charset=utf-8'
    return response


@app.route("/-/index")
def pageindex():
    idx = PageIndex()
    return idx.render()


@app.route("/-/api/v1/pages")
def page_suggestions():
    if not otterwiki.auth.has_permission("WRITE"):
        abort(403)
    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    return jsonify(
        {
            "pages": search_page_paths(
                request.args.get("q", ""),
                limit=limit,
            )
        }
    )


@app.route("/-/create", methods=["POST", "GET"])
def create():
    pagename = request.form.get("pagename")
    pagename_sanitized = sanitize_pagename(pagename)
    if pagename is None:
        # This is the default create page view
        return render_template(
            "create.html",
            title="新建页面",
            pagename_prefixes=get_pagename_prefixes(),
            menutree=SidebarPageIndex("/").query(),
            custom_menu=SidebarMenu().query(),
        )
    elif pagename != pagename_sanitized:
        if pagename is not None and pagename != pagename_sanitized:
            toast("请检查页面名称。", "warning")
        return render_template(
            "create.html",
            title="新建页面",
            pagename=pagename_sanitized,
            pagename_prefixes=get_pagename_prefixes(),
            menutree=SidebarPageIndex("/").query(),
            custom_menu=SidebarMenu().query(),
        )
    else:
        # this is the creation of a new page
        p = Page(pagename=pagename)
        return p.create()


#
# user login/logout/settings
#
@app.route("/-/login", methods=["POST", "GET"])
def login():
    email = request.cookies.get("email")
    if request.method == "GET":
        return otterwiki.auth.login_form(email)
    else:
        return otterwiki.auth.handle_login(
            email=request.form.get("email"),
            password=request.form.get("password"),
            remember=request.form.get("remember"),
        )


@app.route("/-/register", methods=["POST", "GET"])
def register():
    if request.method == "GET":
        return otterwiki.auth.register_form()
    else:
        return otterwiki.auth.handle_register(
            email=request.form.get("email"),
            name=request.form.get("name"),
            password1=request.form.get("password1"),
            password2=request.form.get("password2"),
        )


@app.route("/-/logout")
@login_required
def logout():
    return otterwiki.auth.handle_logout()


@app.route("/-/lost_password", methods=["POST", "GET"])
def lost_password():
    if request.method == "GET":
        return otterwiki.auth.lost_password_form()
    else:
        return otterwiki.auth.handle_recover_password(
            email=request.form.get("email"),
        )


@app.route("/-/confirm_email/<string:token>", methods=["POST", "GET"])
def confirm_email(token):
    return otterwiki.auth.handle_confirmation(token)


@app.route("/-/recover_password/<string:token>", methods=["GET"])
def recover_password(token):
    return otterwiki.auth.handle_recover_password_token(token=token)


@app.route("/-/request_confirmation_link/<string:email>", methods=["GET"])
def request_confirmation_link(email):
    return otterwiki.auth.handle_request_confirmation(email=email)


#
# page views
#
@app.route("/<path:path>/view/<string:revision>")
@app.route("/<path:path>/view")
def pageview(path="Home", revision=None):
    p = Page(path, revision=revision)
    return p.view()


# last matching endpoint seems to be the default for url_for
@app.route("/<path:path>")
def view(path="Home"):
    p = AutoRoute(path, values=request.values)
    return p.view()


@app.route("/<path:path>/history", methods=["POST", "GET"])
def history(path):
    # return "path={}".format(path)
    p = Page(path)
    return p.history(
        rev_a=request.form.get("rev_a"),
        rev_b=request.form.get("rev_b"),
    )


@app.route(
    "/<path:path>/diff/<string:rev_a>/<string:rev_b>", methods=["POST", "GET"]
)
def diff(path, rev_a, rev_b):
    # return "path={}".format(path)
    p = Page(path)
    return p.diff(
        rev_a=rev_a,
        rev_b=rev_b,
    )


@app.route("/<path:path>/rename/", methods=["POST", "GET"])
@app.route("/<path:path>/rename", methods=["POST", "GET"])
def rename(path):
    p = Page(path)
    if request.method == "POST":
        return p.handle_rename(
            new_pagename=request.form.get("new_pagename"),
            message=request.form.get("message"),
            author=otterwiki.auth.get_author(),
            update_backlinks=request.form.get("update_backlinks"),
        )
    return p.rename_form()


@app.route("/<path:path>/delete/", methods=["POST", "GET"])
@app.route("/<path:path>/delete", methods=["POST", "GET"])
def delete(path):
    p = Page(path)
    if request.method == "POST":
        return p.delete(
            message=request.form.get("message"),
            author=otterwiki.auth.get_author(),
            recursive=request.form.get("recursive", False) == "recursive",
        )
    return p.delete_form()


@app.route("/<path:path>/blame/", methods=["GET"])
@app.route("/<path:path>/blame", methods=["GET"])
@app.route("/<path:path>/blame/<string:revision>", methods=["GET"])
def blame(path, revision=None):
    p = Page(path, revision=revision)
    return p.blame()


@app.route("/<path:path>/edit", methods=["POST", "GET"])
@app.route("/<path:path>/edit/<string:revision>", methods=["GET"])
def edit(path, revision=None):

    p = Page(path, revision=revision)
    return p.editor(
        author=otterwiki.auth.get_author(),
        handle_draft=request.form.get("draft", None),
    )


@app.route("/<path:path>/save", methods=["POST"])
def save(path):
    # fetch form
    content = request.form.get("content", "")
    # commit message
    commit = request.form.get("commit", "").strip()
    # Note: cursor_line cursor_ch are in the form
    # clean form data (make sure last character is a newline
    content = content.replace("\r\n", "\n").strip() + "\n"
    commit = commit.strip()
    # create page object
    p = Page(path)
    # and save
    return p.save(
        content=content, commit=commit, author=otterwiki.auth.get_author()
    )


@app.route("/<path:path>/preview", methods=["POST", "GET"])
def preview(path):
    p = Page(path)
    return p.preview(
        content=request.form.get("content"),
        cursor_line=request.form.get("cursor_line"),
    )


@app.route("/<path:path>/draft", methods=["POST", "GET"])
def draft(path):
    p = Page(path)
    return p.save_draft(
        content=request.form.get("content", ""),
        cursor_line=request.form.get("cursor_line", 0),
        cursor_ch=request.form.get("cursor_ch", 0),
        revision=request.form.get("revision", ""),
        author=otterwiki.auth.get_author(),
    )


@app.route("/<path:pagepath>/source/<string:revision>")
@app.route("/<path:pagepath>/source", methods=["GET"])
def source(pagepath, revision=None):
    raw = 'raw' in request.args
    p = Page(pagepath, revision=revision)
    return p.source(raw=raw)


@app.route("/-/commit/<string:revision>", methods=["GET"])
def show_commit(revision):
    chlg = Changelog()
    return chlg.show_commit(revision)


@app.route("/-/revert/<string:revision>", methods=["POST", "GET"])
def revert(revision):
    message = request.form.get("message")
    chlg = Changelog()
    if request.method == "POST":
        return chlg.revert(
            revision=revision,
            message=message,
            author=otterwiki.auth.get_author(),
        )
    return chlg.revert_form(revision=revision, message=message)


#
# page attachments
#


@app.route("/<path:pagepath>/a/<string:filename>")
@app.route("/<path:pagepath>/a/<string:filename>/<string:revision>")
def get_attachment(pagepath, filename, revision=None):
    p = Page(pagepath)
    if revision is None:
        revision = request.args.get("revision", None)
    return p.get_attachment(filename, revision)


@app.route("/<path:pagepath>/t/<string:filename>")
@app.route("/<path:pagepath>/t/<string:filename>/<int:size>")
def get_attachment_thumbnail(pagepath, filename, size=80):
    p = Page(pagepath)
    return p.get_attachment_thumbnail(
        filename=filename, size=size, revision=None
    )


@app.route(
    "/<path:pagepath>/attachment/<string:filename>", methods=["POST", "GET"]
)
def edit_attachment(pagepath, filename):
    p = Page(pagepath)
    return p.edit_attachment(
        filename=filename,
        new_filename=request.form.get("new_filename"),
        message=request.form.get("message"),
        delete=request.form.get("delete"),
        author=otterwiki.auth.get_author(),
    )


@app.route("/<path:pagepath>/attachments", methods=["POST", "GET"])
def attachments(pagepath):
    p = Page(pagepath)
    if request.method == "POST":
        return p.upload_attachments(
            files=request.files.getlist("file"),
            message=request.form.get("message"),
            filename=request.form.get("filename"),
            author=otterwiki.auth.get_author(),
        )
    return p.render_attachments()


@app.route("/<path:pagepath>/inline_attachment", methods=["POST"])
def inline_attachment(pagepath):
    p = Page(pagepath)
    return p.upload_attachments(
        files=request.files.getlist("file"),
        message="Uploaded via inline attachment",
        filename=None,
        author=otterwiki.auth.get_author(),
        inline=True,
    )


#
# search
#


@app.route("/-/search", methods=["POST", "GET"])
@app.route("/-/search/<string:query>", methods=["POST", "GET"])
def search(query=None):
    if query is None:
        query = request.form.get("query")
    s = Search(
        query=query,
        is_casesensitive=request.form.get("is_casesensitive") == "y",
        is_regexp=request.form.get("is_regexp") == "y",
        in_history=request.form.get("in_history") == "y",
    )
    return s.render()


#
# git remote http server
#
@app.route("/.git", methods=["GET"])
def dotgit():
    return redirect(url_for("index"))


@app.route("/.git/info/refs", methods=["POST", "GET"])
@csrf.exempt
def git_info_refs():
    service = request.args.get("service")
    if service in ["git-upload-pack", "git-receive-pack"]:
        return githttpserver.advertise_refs(service)
    else:
        abort(400)


@app.route("/.git/git-upload-pack", methods=["POST"])
@csrf.exempt
def git_upload_pack():
    return githttpserver.git_upload_pack(request.stream)


@app.route("/.git/git-receive-pack", methods=["POST"])
@csrf.exempt
def git_receive_pack():
    return githttpserver.git_receive_pack(request.stream)


@app.route("/-/api/v1/pull/<string:webhook_hash>", methods=["POST", "GET"])
@csrf.exempt
def pull_webhook(webhook_hash):
    """
    Webhook endpoint for triggering git pulls from remote repositories.
    The webhook_hash should match the HMAC-SHA256 hash generated from remote_url using SECRET_KEY.
    """
    from otterwiki.repomgmt import get_repo_manager

    if not app.config.get('GIT_REMOTE_PULL_ENABLED'):
        abort(404)

    remote_url = app.config.get('GIT_REMOTE_PULL_URL')
    if not remote_url:
        abort(404)

    if app.config.get('GIT_REMOTE_PULL_URL_SECURE'):
        expected_hash = compute_webhook_hash(
            app.config['SECRET_KEY'], remote_url
        )
    else:
        expected_hash = compute_webhook_hash_legacy(remote_url)

    if webhook_hash != expected_hash:
        abort(404)

    repo_manager = get_repo_manager()
    success = repo_manager.auto_pull_webhook() if repo_manager else False

    if success:
        return jsonify(
            {"status": "success", "message": "Pull triggered successfully"}
        )
    else:
        return (
            jsonify({"status": "error", "message": "Failed to trigger pull"}),
            500,
        )


@app.route("/-/plugin/<string:name>/<string:extra>", methods=["POST", "GET"])
def plugin_url_request(name, extra):
    result = call_hook(
        "url_request", plugin=name, extra=extra, values=request.values
    )
    if not result:
        abort(404)
    return result


@app.route(
    "/-/admin/plugin/<string:name>/<string:extra>", methods=["POST", "GET"]
)
def plugin_url_admin_request(name, extra):
    if not otterwiki.auth.has_permission("ADMIN"):
        abort(403)
    result = call_hook(
        "url_admin_request", plugin=name, extra=extra, valuies=request.values
    )
    if not result:
        abort(404)
    return result


@app.route("/-/plugin-static.css")
def plugin_static_css():
    static_css = g.get("plugin_static_css")
    if static_css is None:
        t_start = timer()
        static_css = "" + "\n".join(collect_hook("static_css"))
        if timer() - t_start > 0.05:
            app.logger.info(
                f"Generating plugin_static_css took {timer() - t_start:.3f}s."
            )
        g.plugin_static_css = static_css
    response = make_response(
        static_css,
        200,
    )
    response.mimetype = "text/css"
    response.headers['Cache-Control'] = "max-age=300"
    return response


#
# Error handling
#


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    app.logger.warning(f"CSRF Error: {e.description} url: {request.url}")
    return (
        f"""<!doctype html>
<html lang=en>
<title>400 Bad Request</title>
<h1>Bad Request</h1>
<p>{e.description}</p>
""",
        400,
    )
