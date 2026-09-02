(function () {
    "use strict";

    var config = window.interfaceTransactionAssetsConfig || {};
    var statusLabels = {
        DRAFT: "草稿",
        IDENTIFIED: "已识别",
        CONFIRMED: "已确认",
        PUBLISHED: "已发布",
        DEPRECATED: "已废弃",
        OFFLINE: "已下线"
    };

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

    function templateUrl(template, value) {
        return String(template || "").replace("__ASSET_ID__", encodeURIComponent(value));
    }

    function textCell(text) {
        var element = document.createElement("td");
        var value = String(text || "");
        element.textContent = value || "--";
        element.title = value;
        return element;
    }

    function showFeedback(feedback, message) {
        feedback.className = "alert system-feedback alert-danger";
        feedback.textContent = message;
        feedback.hidden = false;
    }

    function statusText(status) {
        var key = String(status || "").toUpperCase();
        return statusLabels[key] || status || "--";
    }

    function initializeDetail() {
        var feedback = document.getElementById("transaction-asset-feedback");
        var tableBody = document.getElementById("transaction-asset-field-table-body");
        var fieldGroups = {inputFields: [], outputFields: [], propertyFields: []};
        var activeGroup = "inputFields";

        function setText(id, value) {
            var element = document.getElementById(id);
            var text = String(value || "");
            element.textContent = text || "--";
            element.title = text;
        }

        function renderFields() {
            var records = fieldGroups[activeGroup] || [];
            tableBody.textContent = "";
            if (!records.length) {
                var emptyRow = document.createElement("tr");
                var emptyCell = textCell("暂无数据");
                emptyCell.className = "system-table-state";
                emptyCell.colSpan = 3;
                emptyRow.appendChild(emptyCell);
                tableBody.appendChild(emptyRow);
                return;
            }
            records.forEach(function (record) {
                var row = document.createElement("tr");
                row.appendChild(textCell(record.fieldCode));
                row.appendChild(textCell(record.fieldName));
                row.appendChild(textCell(record.fieldType));
                tableBody.appendChild(row);
            });
        }

        Array.prototype.forEach.call(
            document.querySelectorAll(".transaction-asset-field-tab"),
            function (tab) {
                tab.addEventListener("click", function () {
                    activeGroup = tab.dataset.fieldGroup;
                    Array.prototype.forEach.call(
                        document.querySelectorAll(".transaction-asset-field-tab"),
                        function (item) {
                            var active = item === tab;
                            item.classList.toggle("active", active);
                            item.setAttribute("aria-selected", active ? "true" : "false");
                        }
                    );
                    renderFields();
                });
            }
        );

        requestJson(templateUrl(config.assetUrlTemplate, config.assetId)).then(function (body) {
            var detail = body.data || {};
            setText("transaction-detail-asset-code", detail.assetCode);
            setText("transaction-detail-asset-name", detail.assetName);
            setText("transaction-detail-asset-type", detail.assetType);
            setText("transaction-detail-module-snapshot-id", detail.moduleSnapshotId);
            setText("transaction-detail-application-name", detail.applicationName);
            setText("transaction-detail-app-version", detail.appVersion);
            setText("transaction-detail-revision-no", detail.revisionNo);
            setText("transaction-detail-source-file", detail.sourceFile);
            fieldGroups.inputFields = detail.inputFields || [];
            fieldGroups.outputFields = detail.outputFields || [];
            fieldGroups.propertyFields = detail.propertyFields || [];
            renderFields();
        }).catch(function (error) {
            tableBody.textContent = "";
            var errorRow = document.createElement("tr");
            var errorCell = textCell("交易资产详情加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 3;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(feedback, error.message || "交易资产详情加载失败。");
        });
    }

    function initializeList() {
        var initial = config.initialFilters || {};
        var state = {
            pageNo: 1,
            pageSize: 10,
            total: 0,
            records: [],
            loading: false,
            systems: [],
            applications: [],
            snapshots: [],
            modules: [],
            filters: {
                systemId: String(initial.systemId || ""),
                appId: String(initial.appId || ""),
                appSnapshotId: String(initial.appSnapshotId || ""),
                moduleSnapshotId: String(initial.moduleSnapshotId || ""),
                assetType: String(initial.assetType || ""),
                status: String(initial.status || ""),
                exposed: String(initial.exposed || ""),
                appVersion: String(initial.appVersion || ""),
                revisionNo: String(initial.revisionNo || ""),
                keyword: String(initial.keyword || "")
            }
        };
        var form = document.getElementById("transaction-asset-filters");
        var feedback = document.getElementById("transaction-asset-feedback");
        var tableBody = document.getElementById("transaction-asset-table-body");
        var totalElement = document.getElementById("transaction-asset-total");
        var currentPageElement = document.getElementById("transaction-asset-current-page");
        var pageSizeElement = document.getElementById("transaction-asset-page-size");
        var pageJumpElement = document.getElementById("transaction-asset-page-jump");
        var prevButton = document.getElementById("transaction-asset-prev-page");
        var nextButton = document.getElementById("transaction-asset-next-page");
        var inputs = {
            systemId: document.getElementById("transaction-asset-system"),
            appId: document.getElementById("transaction-asset-application"),
            appSnapshotId: document.getElementById("transaction-asset-app-snapshot"),
            moduleSnapshotId: document.getElementById("transaction-asset-module-snapshot"),
            assetType: document.getElementById("transaction-asset-type"),
            status: document.getElementById("transaction-asset-status"),
            exposed: document.getElementById("transaction-asset-exposed"),
            appVersion: document.getElementById("transaction-asset-app-version"),
            revisionNo: document.getElementById("transaction-asset-revision-no"),
            keyword: document.getElementById("transaction-asset-keyword")
        };
        var filterTimer = null;

        function setOptions(select, records, valueKey, labelBuilder, selected) {
            select.textContent = "";
            var blank = document.createElement("option");
            blank.value = "";
            blank.textContent = "请选择";
            select.appendChild(blank);
            records.forEach(function (record) {
                var value = String(record[valueKey] || "");
                if (!value) {
                    return;
                }
                var option = document.createElement("option");
                option.value = value;
                option.textContent = labelBuilder(record) || value;
                select.appendChild(option);
            });
            if (selected && !Array.prototype.some.call(select.options, function (option) {
                return option.value === selected;
            })) {
                var retained = document.createElement("option");
                retained.value = selected;
                retained.textContent = selected;
                select.appendChild(retained);
            }
            select.value = selected || "";
        }

        function refreshApplicationOptions() {
            var records = state.applications.filter(function (record) {
                return !state.filters.systemId || record.systemId === state.filters.systemId;
            });
            setOptions(inputs.appId, records, "appId", function (record) {
                return record.appName || record.appCode || record.appId;
            }, state.filters.appId);
        }

        function refreshSnapshotOptions() {
            var records = state.snapshots.filter(function (record) {
                return !state.filters.appId || record.appId === state.filters.appId;
            });
            setOptions(inputs.appSnapshotId, records, "snapshotId", function (record) {
                var suffix = record.appVersion ? " / " + record.appVersion : "";
                return record.snapshotId + suffix;
            }, state.filters.appSnapshotId);
        }

        function refreshModuleOptions() {
            var records = state.modules.filter(function (record) {
                var appMatches = !state.filters.appId || record.appId === state.filters.appId;
                var snapshotMatches = !state.filters.appSnapshotId || record.appSnapshotId === state.filters.appSnapshotId;
                return appMatches && snapshotMatches;
            });
            setOptions(inputs.moduleSnapshotId, records, "moduleSnapshotId", function (record) {
                return record.artifactId
                    ? record.moduleSnapshotId + " / " + record.artifactId
                    : record.moduleSnapshotId;
            }, state.filters.moduleSnapshotId);
        }

        function renderRows() {
            tableBody.textContent = "";
            if (!state.records.length) {
                var emptyRow = document.createElement("tr");
                var emptyCell = textCell("暂无交易资产数据");
                emptyCell.className = "system-table-state";
                emptyCell.colSpan = 8;
                emptyRow.appendChild(emptyCell);
                tableBody.appendChild(emptyRow);
                return;
            }
            state.records.forEach(function (record) {
                var row = document.createElement("tr");
                row.appendChild(textCell(record.assetCode));
                row.appendChild(textCell(record.assetName));
                row.appendChild(textCell(record.assetType));
                row.appendChild(textCell(record.moduleSnapshotId));
                row.appendChild(textCell(record.appVersion));
                row.appendChild(textCell(record.revisionNo));
                var statusCell = document.createElement("td");
                var status = document.createElement("span");
                var statusKey = String(record.status || "").toLowerCase();
                status.className = "transaction-asset-status " + statusKey;
                status.textContent = statusText(record.status);
                statusCell.appendChild(status);
                row.appendChild(statusCell);
                var actionCell = document.createElement("td");
                var detail = document.createElement("a");
                detail.className = "system-table-action";
                detail.textContent = "查看详情";
                if (record.assetId) {
                    detail.href = templateUrl(config.detailPageUrlTemplate, record.assetId);
                } else {
                    detail.setAttribute("aria-disabled", "true");
                }
                actionCell.appendChild(detail);
                row.appendChild(actionCell);
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

        function collectFilters() {
            Object.keys(inputs).forEach(function (key) {
                state.filters[key] = inputs[key].value.trim();
            });
        }

        function queryParameters(includePage) {
            var parameters = [];
            if (includePage) {
                parameters.push("pageNo=" + encodeURIComponent(state.pageNo));
                parameters.push("pageSize=" + encodeURIComponent(state.pageSize));
            }
            Object.keys(state.filters).forEach(function (key) {
                if (state.filters[key]) {
                    parameters.push(encodeURIComponent(key) + "=" + encodeURIComponent(state.filters[key]));
                }
            });
            return parameters;
        }

        function syncBrowserUrl() {
            if (!window.history || !window.history.replaceState) {
                return;
            }
            var parameters = queryParameters(false);
            var url = config.listPageUrl + (parameters.length ? "?" + parameters.join("&") : "");
            window.history.replaceState(null, "", url);
        }

        function loadAssets(pageNo) {
            state.loading = true;
            state.pageNo = Math.max(1, pageNo || 1);
            feedback.hidden = true;
            renderPagination();
            tableBody.textContent = "";
            var loadingRow = document.createElement("tr");
            var loadingCell = textCell("正在加载...");
            loadingCell.className = "system-table-state";
            loadingCell.colSpan = 8;
            loadingRow.appendChild(loadingCell);
            tableBody.appendChild(loadingRow);

            requestJson(config.assetsUrl + "?" + queryParameters(true).join("&")).then(function (body) {
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
                var errorCell = textCell("交易资产列表加载失败");
                errorCell.className = "system-table-state";
                errorCell.colSpan = 8;
                errorRow.appendChild(errorCell);
                tableBody.appendChild(errorRow);
                showFeedback(feedback, error.message || "交易资产列表加载失败。");
            }).finally(function () {
                state.loading = false;
                renderPagination();
            });
        }

        function applyFilters() {
            collectFilters();
            syncBrowserUrl();
            loadAssets(1);
        }

        function loadFilterOptions() {
            return Promise.all([
                requestJson(config.systemsUrl + "?pageNo=1&pageSize=100"),
                requestJson(config.applicationsUrl + "?pageNo=1&pageSize=100"),
                requestJson(config.applicationSnapshotListUrl + "?pageNo=1&pageSize=100"),
                requestJson(config.moduleSnapshotsUrl + "?pageNo=1&pageSize=100")
            ]).then(function (responses) {
                state.systems = responses[0].data.records || [];
                state.applications = responses[1].data.records || [];
                state.snapshots = responses[2].data.records || [];
                state.modules = responses[3].data.records || [];
                setOptions(inputs.systemId, state.systems, "systemId", function (record) {
                    return record.systemName || record.systemCode || record.systemId;
                }, state.filters.systemId);
                refreshApplicationOptions();
                refreshSnapshotOptions();
                refreshModuleOptions();
            }).catch(function (error) {
                showFeedback(feedback, error.message || "筛选项加载失败。");
            });
        }

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            if (filterTimer) {
                window.clearTimeout(filterTimer);
            }
            applyFilters();
        });
        inputs.systemId.addEventListener("change", function () {
            state.filters.systemId = inputs.systemId.value;
            state.filters.appId = "";
            state.filters.appSnapshotId = "";
            state.filters.moduleSnapshotId = "";
            refreshApplicationOptions();
            refreshSnapshotOptions();
            refreshModuleOptions();
            applyFilters();
        });
        inputs.appId.addEventListener("change", function () {
            state.filters.appId = inputs.appId.value;
            state.filters.appSnapshotId = "";
            state.filters.moduleSnapshotId = "";
            refreshSnapshotOptions();
            refreshModuleOptions();
            applyFilters();
        });
        inputs.appSnapshotId.addEventListener("change", function () {
            state.filters.appSnapshotId = inputs.appSnapshotId.value;
            state.filters.moduleSnapshotId = "";
            refreshModuleOptions();
            applyFilters();
        });
        [inputs.moduleSnapshotId, inputs.assetType, inputs.status, inputs.exposed].forEach(function (input) {
            input.addEventListener("change", applyFilters);
        });
        [inputs.appVersion, inputs.revisionNo, inputs.keyword].forEach(function (input) {
            input.addEventListener("input", function () {
                if (filterTimer) {
                    window.clearTimeout(filterTimer);
                }
                filterTimer = window.setTimeout(applyFilters, 450);
            });
        });
        prevButton.addEventListener("click", function () { loadAssets(state.pageNo - 1); });
        nextButton.addEventListener("click", function () { loadAssets(state.pageNo + 1); });
        pageSizeElement.addEventListener("change", function () {
            state.pageSize = Number(pageSizeElement.value) || 10;
            loadAssets(1);
        });

        function jumpToPage() {
            var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
            var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
            loadAssets(target);
        }

        pageJumpElement.addEventListener("change", jumpToPage);
        pageJumpElement.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                jumpToPage();
            }
        });

        Object.keys(inputs).forEach(function (key) {
            inputs[key].value = state.filters[key];
        });
        loadAssets(1);
        loadFilterOptions();
    }

    if (config.mode === "detail") {
        initializeDetail();
    } else {
        initializeList();
    }
}());
