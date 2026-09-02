(function () {
    "use strict";

    var config = window.interfaceModuleSnapshotsConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        applications: [],
        filterAppId: String(config.initialAppId || ""),
        filterAppSnapshotId: String(config.initialAppSnapshotId || ""),
        loading: false
    };
    var tableBody = document.getElementById("module-snapshot-table-body");
    var totalElement = document.getElementById("module-snapshot-total");
    var currentPageElement = document.getElementById("module-snapshot-current-page");
    var pageSizeElement = document.getElementById("module-snapshot-page-size");
    var pageJumpElement = document.getElementById("module-snapshot-page-jump");
    var prevButton = document.getElementById("module-snapshot-prev-page");
    var nextButton = document.getElementById("module-snapshot-next-page");
    var feedback = document.getElementById("module-snapshot-feedback");
    var appFilter = document.getElementById("module-snapshot-app-filter");
    var appOptions = document.getElementById("module-snapshot-app-options");
    var appClearButton = document.getElementById("module-snapshot-app-clear");
    var snapshotFilter = document.getElementById("module-snapshot-id");

    function requestJson(url) {
        return fetch(url, {headers: {Accept: "application/json"}}).then(function (response) {
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

    function showFeedback(message) {
        feedback.className = "alert system-feedback alert-danger";
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

    function transactionAssetLink(record) {
        var link = document.createElement("a");
        link.className = "system-table-action";
        link.textContent = "查看交易资产";
        if (record.moduleSnapshotId) {
            link.href = config.transactionAssetsUrl + "?moduleSnapshotId=" + encodeURIComponent(record.moduleSnapshotId);
        } else {
            link.setAttribute("aria-disabled", "true");
        }
        return link;
    }

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无模块快照数据");
            emptyCell.className = "system-table-state";
            emptyCell.colSpan = 7;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.applicationName));
            row.appendChild(cell(record.artifactId));
            row.appendChild(cell(record.groupId));
            row.appendChild(cell(record.jarName));
            row.appendChild(cell(record.jarPath));
            row.appendChild(cell(record.createdAt));
            var actions = document.createElement("td");
            actions.appendChild(transactionAssetLink(record));
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
        if (state.filterAppSnapshotId) {
            parameters.push("appSnapshotId=" + encodeURIComponent(state.filterAppSnapshotId));
        }
        return config.moduleSnapshotsUrl + "?" + parameters.join("&");
    }

    function loadSnapshots(pageNo) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        feedback.hidden = true;
        renderPagination();
        tableBody.textContent = "";
        var loadingRow = document.createElement("tr");
        var loadingCell = cell("正在加载...");
        loadingCell.className = "system-table-state";
        loadingCell.colSpan = 7;
        loadingRow.appendChild(loadingCell);
        tableBody.appendChild(loadingRow);

        requestJson(listUrl()).then(function (body) {
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
            var errorCell = cell("模块快照列表加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 7;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "模块快照列表加载失败。");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function loadApplications() {
        return requestJson(config.applicationsUrl + "?pageNo=1&pageSize=100").then(function (body) {
            state.applications = body.data.records || [];
            appOptions.textContent = "";
            state.applications.forEach(function (application) {
                var option = document.createElement("option");
                option.value = application.appId;
                option.label = application.appName || application.appCode || application.appId;
                appOptions.appendChild(option);
            });
        }).catch(function (error) {
            showFeedback(error.message || "应用列表加载失败。");
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
        var match = state.applications.some(function (application) {
            return application.appId === value;
        });
        if (!match && state.applications.length) {
            showFeedback("请选择有效的应用 ID。");
            return;
        }
        state.filterAppId = value;
        loadSnapshots(1);
    }

    function applySnapshotFilter() {
        state.filterAppSnapshotId = snapshotFilter.value.trim();
        loadSnapshots(1);
    }

    appFilter.addEventListener("change", applyApplicationFilter);
    appFilter.addEventListener("input", function () {
        appClearButton.hidden = !appFilter.value.trim();
        if (!appFilter.value.trim() && state.filterAppId) {
            applyApplicationFilter();
            return;
        }
        if (state.applications.some(function (application) { return application.appId === appFilter.value.trim(); })) {
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
    document.getElementById("module-snapshot-search").addEventListener("click", applySnapshotFilter);
    snapshotFilter.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applySnapshotFilter();
        }
    });
    snapshotFilter.addEventListener("input", function () {
        if (!snapshotFilter.value && state.filterAppSnapshotId) {
            applySnapshotFilter();
        }
    });
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
    snapshotFilter.value = state.filterAppSnapshotId;
    loadSnapshots(1);
    loadApplications();
}());
