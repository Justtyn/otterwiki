(function () {
    "use strict";

    var config = window.interfaceApplicationSnapshotsConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        applications: [],
        filterAppId: String(config.initialAppId || ""),
        filterAppVersion: String(config.initialAppVersion || ""),
        loading: false
    };
    var tableBody = document.getElementById("application-snapshot-table-body");
    var totalElement = document.getElementById("application-snapshot-total");
    var currentPageElement = document.getElementById("application-snapshot-current-page");
    var pageSizeElement = document.getElementById("application-snapshot-page-size");
    var pageJumpElement = document.getElementById("application-snapshot-page-jump");
    var prevButton = document.getElementById("application-snapshot-prev-page");
    var nextButton = document.getElementById("application-snapshot-next-page");
    var feedback = document.getElementById("application-snapshot-feedback");
    var appFilter = document.getElementById("application-snapshot-app-filter");
    var appOptions = document.getElementById("application-snapshot-app-options");
    var appClearButton = document.getElementById("application-snapshot-app-clear");
    var versionFilter = document.getElementById("application-snapshot-version");

    function requestJson(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        options.headers.Accept = "application/json";
        if (options.method && options.method !== "GET") {
            options.headers["X-CSRFToken"] = config.csrfToken;
        }
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () {
                throw new Error("服务器没有返回有效的 JSON 数据。");
            }).then(function (body) {
                if (!response.ok || body.code !== 200) {
                    throw new Error(body.msg || body.message || "请求失败。");
                }
                return body;
            });
        });
    }

    function showFeedback(message, type) {
        feedback.className = "alert system-feedback " + (type === "success" ? "alert-success" : "alert-danger");
        feedback.textContent = message;
        feedback.hidden = false;
    }

    function cell(text) {
        var element = document.createElement("td");
        var value = String(text || "");
        element.textContent = value || "--";
        element.title = value;
        return element;
    }

    function actionButton(label, className, handler, disabled) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "system-table-action " + className;
        button.textContent = label;
        button.disabled = disabled;
        button.addEventListener("click", handler);
        return button;
    }

    function moduleSnapshotLink(record) {
        var link = document.createElement("a");
        link.className = "system-table-action";
        link.textContent = "查看模块快照";
        if (record.snapshotId) {
            var parameters = ["appSnapshotId=" + encodeURIComponent(record.snapshotId)];
            if (record.appId) {
                parameters.push("appId=" + encodeURIComponent(record.appId));
            }
            link.href = config.moduleSnapshotsUrl + "?" + parameters.join("&");
        } else {
            link.setAttribute("aria-disabled", "true");
        }
        return link;
    }

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无应用快照数据");
            emptyCell.className = "system-table-state";
            emptyCell.colSpan = 10;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.applicationName));
            row.appendChild(cell(record.appVersion));
            row.appendChild(cell(record.revisionNo));
            row.appendChild(cell(record.packageName));
            row.appendChild(cell(record.packageHash));
            row.appendChild(cell(record.assetHash));
            row.appendChild(cell(record.snapshotId));
            row.appendChild(cell(record.previousSnapshotId));
            row.appendChild(cell(record.createdAt));

            var actions = document.createElement("td");
            actions.appendChild(moduleSnapshotLink(record));
            actions.appendChild(actionButton("删除", "delete", function () {
                deleteSnapshot(record);
            }, !record.snapshotId));
            row.appendChild(actions);
            tableBody.appendChild(row);
        });
    }

    function renderPagination() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        totalElement.textContent = state.total.toLocaleString();
        currentPageElement.textContent = state.pageNo;
        pageJumpElement.value = state.pageNo;
        pageJumpElement.max = pageCount;
        prevButton.disabled = state.loading || state.pageNo <= 1;
        nextButton.disabled = state.loading || state.pageNo >= pageCount;
    }

    function listUrl() {
        var parameters = [
            "pageNo=" + encodeURIComponent(state.pageNo),
            "pageSize=" + encodeURIComponent(state.pageSize)
        ];
        if (state.filterAppId) {
            parameters.push("appId=" + encodeURIComponent(state.filterAppId));
        }
        if (state.filterAppVersion) {
            parameters.push("appVersion=" + encodeURIComponent(state.filterAppVersion));
        }
        return config.snapshotsUrl + "?" + parameters.join("&");
    }

    function loadSnapshots(pageNo, preserveFeedback) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        if (!preserveFeedback) {
            feedback.hidden = true;
        }
        renderPagination();
        tableBody.textContent = "";
        var loadingRow = document.createElement("tr");
        var loadingCell = cell("正在加载...");
        loadingCell.className = "system-table-state";
        loadingCell.colSpan = 10;
        loadingRow.appendChild(loadingCell);
        tableBody.appendChild(loadingRow);

        requestJson(listUrl(), {method: "GET"}).then(function (body) {
            state.records = body.data.records || [];
            state.total = body.data.total || 0;
            state.pageNo = body.data.pageNo || state.pageNo;
            state.pageSize = body.data.pageSize || state.pageSize;
            pageSizeElement.value = String(state.pageSize);
            renderRows();
        }).catch(function (error) {
            state.records = [];
            tableBody.textContent = "";
            var errorRow = document.createElement("tr");
            var errorCell = cell("应用快照列表加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 10;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "应用快照列表加载失败。", "error");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function applicationLabel(application) {
        var name = application.appName || application.appCode || application.appId;
        return application.appCode && application.appCode !== name ? name + "（" + application.appCode + "）" : name;
    }

    function loadApplications() {
        return requestJson(config.applicationsUrl + "?pageNo=1&pageSize=100", {method: "GET"}).then(function (body) {
            state.applications = body.data.records || [];
            appOptions.textContent = "";
            state.applications.forEach(function (application) {
                var option = document.createElement("option");
                option.value = applicationLabel(application);
                appOptions.appendChild(option);
            });
            if (state.filterAppId) {
                var selected = state.applications.find(function (application) {
                    return application.appId === state.filterAppId;
                });
                appFilter.value = selected ? applicationLabel(selected) : state.filterAppId;
                appClearButton.hidden = false;
            }
        }).catch(function (error) {
            showFeedback(error.message || "应用列表加载失败。", "error");
        });
    }

    function applyApplicationFilter() {
        var value = appFilter.value.trim();
        appClearButton.hidden = !value;
        if (!value) {
            state.filterAppId = "";
            loadSnapshots(1);
            return;
        }
        var match = state.applications.find(function (application) {
            return applicationLabel(application) === value || application.appId === value;
        });
        if (!match) {
            showFeedback("请选择有效的应用。", "error");
            return;
        }
        state.filterAppId = match.appId;
        loadSnapshots(1);
    }

    function applyVersionFilter() {
        state.filterAppVersion = versionFilter.value.trim();
        loadSnapshots(1);
    }

    function deleteSnapshot(record) {
        if (!window.confirm("确定删除该应用快照吗？")) {
            return;
        }
        var deleteUrl = config.deleteSnapshotUrlTemplate.replace("__SNAPSHOT_ID__", encodeURIComponent(record.snapshotId));
        requestJson(deleteUrl, {method: "DELETE"}).then(function () {
            showFeedback("应用快照删除成功。", "success");
            var targetPage = state.records.length === 1 && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadSnapshots(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "应用快照删除失败。", "error");
        });
    }

    function startComparison() {
        var parameters = [];
        if (state.filterAppId) {
            parameters.push("appId=" + encodeURIComponent(state.filterAppId));
        }
        if (state.filterAppVersion) {
            parameters.push("appVersion=" + encodeURIComponent(state.filterAppVersion));
        }
        window.location.href = config.comparisonUrl + (parameters.length ? "?" + parameters.join("&") : "");
    }

    appFilter.addEventListener("change", applyApplicationFilter);
    appFilter.addEventListener("input", function () {
        appClearButton.hidden = !appFilter.value.trim();
        if (!appFilter.value.trim() && state.filterAppId) {
            applyApplicationFilter();
            return;
        }
        var exactMatch = state.applications.some(function (application) {
            return applicationLabel(application) === appFilter.value.trim();
        });
        if (exactMatch) {
            applyApplicationFilter();
        }
    });
    appFilter.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applyApplicationFilter();
        }
    });
    appClearButton.addEventListener("click", function () {
        appFilter.value = "";
        appClearButton.hidden = true;
        applyApplicationFilter();
        appFilter.focus();
    });
    document.getElementById("application-snapshot-search").addEventListener("click", applyVersionFilter);
    versionFilter.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applyVersionFilter();
        }
    });
    versionFilter.addEventListener("input", function () {
        if (!versionFilter.value && state.filterAppVersion) {
            applyVersionFilter();
        }
    });
    document.getElementById("application-snapshot-compare").addEventListener("click", startComparison);
    prevButton.addEventListener("click", function () { loadSnapshots(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadSnapshots(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadSnapshots(1);
    });

    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadSnapshots(target);
    }

    pageJumpElement.addEventListener("change", jumpToPage);
    pageJumpElement.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            jumpToPage();
        }
    });

    appFilter.value = state.filterAppId;
    appClearButton.hidden = !state.filterAppId;
    versionFilter.value = state.filterAppVersion;
    loadSnapshots(1);
    loadApplications();
}());
