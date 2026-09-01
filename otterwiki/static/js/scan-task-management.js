(function () {
    "use strict";

    var config = window.interfaceScanTasksConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        applications: [],
        filterAppId: "",
        loading: false,
        runningTaskId: ""
    };
    var statusLabels = {
        INIT: "初始化",
        RUNNING: "运行中",
        SUCCESS: "成功",
        FAIL: "失败"
    };
    var tableBody = document.getElementById("scan-task-table-body");
    var totalElement = document.getElementById("scan-task-total");
    var currentPageElement = document.getElementById("scan-task-current-page");
    var pageSizeElement = document.getElementById("scan-task-page-size");
    var pageJumpElement = document.getElementById("scan-task-page-jump");
    var prevButton = document.getElementById("scan-task-prev-page");
    var nextButton = document.getElementById("scan-task-next-page");
    var feedback = document.getElementById("scan-task-feedback");
    var filterInput = document.getElementById("scan-application-filter");
    var filterOptions = document.getElementById("scan-application-options");
    var filterClearButton = document.getElementById("scan-application-clear");
    var drawer = document.getElementById("scan-task-drawer");
    var backdrop = document.getElementById("scan-task-drawer-backdrop");
    var form = document.getElementById("scan-task-form");
    var submitButton = document.getElementById("scan-task-form-submit");
    var fields = {
        application: document.getElementById("scan-task-application"),
        mavenFields: document.getElementById("scan-task-maven-fields"),
        uploadFields: document.getElementById("scan-task-upload-fields"),
        version: document.getElementById("scan-task-version"),
        versionHelp: document.getElementById("scan-task-version-help"),
        packageName: document.getElementById("scan-task-package-name"),
        file: document.getElementById("scan-task-file"),
        fileName: document.getElementById("scan-task-file-name")
    };

    function requestJson(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        options.headers.Accept = "application/json";
        if (options.method && options.method !== "GET") {
            options.headers["X-CSRFToken"] = config.csrfToken;
            if (!(options.body instanceof FormData)) {
                options.headers["Content-Type"] = "application/json";
            }
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

    function cell(text, className) {
        var element = document.createElement("td");
        element.textContent = text || "--";
        element.title = text || "";
        if (className) {
            element.className = className;
        }
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

    function snapshotLink(record) {
        var link = document.createElement("a");
        link.className = "system-table-action";
        link.textContent = "查看应用快照";
        link.href = config.snapshotsUrl + "?appId=" + encodeURIComponent(record.appId || "");
        if (!record.appId) {
            link.setAttribute("aria-disabled", "true");
            link.removeAttribute("href");
        }
        return link;
    }

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无扫描任务数据", "system-table-state");
            emptyCell.colSpan = 12;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.applicationName));
            var statusCell = document.createElement("td");
            var status = document.createElement("span");
            status.className = "scan-task-status " + String(record.status || "").toLowerCase();
            status.textContent = statusLabels[record.status] || record.status || "--";
            statusCell.appendChild(status);
            row.appendChild(statusCell);
            row.appendChild(cell(record.packageSourceType));
            row.appendChild(cell(record.packageName));
            row.appendChild(cell(record.packagePath));
            row.appendChild(cell(record.resolvedVersion));
            row.appendChild(cell(record.operator));
            row.appendChild(cell(record.startTime));
            row.appendChild(cell(record.endTime));
            row.appendChild(cell(record.errorMessage));
            row.appendChild(cell(record.createdAt));

            var actions = document.createElement("td");
            var running = state.runningTaskId === record.scanTaskId;
            var runButton = actionButton(running ? "执行中..." : "执行", running ? "scan-task-action-loading" : "run", function () {
                runTask(record);
            }, !record.scanTaskId || record.status === "RUNNING" || running);
            if (running) {
                var spinner = document.createElement("i");
                spinner.className = "fas fa-circle-notch fa-spin";
                runButton.prepend(spinner);
            }
            actions.appendChild(runButton);
            actions.appendChild(snapshotLink(record));
            actions.appendChild(actionButton("删除", "delete", function () {
                deleteTask(record);
            }, !record.scanTaskId || record.status === "RUNNING"));
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

    function loadTasks(pageNo, preserveFeedback) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        if (!preserveFeedback) {
            feedback.hidden = true;
        }
        renderPagination();
        tableBody.textContent = "";
        var row = document.createElement("tr");
        var loadingCell = cell("正在加载...", "system-table-state");
        loadingCell.colSpan = 12;
        row.appendChild(loadingCell);
        tableBody.appendChild(row);

        var url = config.scanTasksUrl + "?pageNo=" + encodeURIComponent(state.pageNo) + "&pageSize=" + encodeURIComponent(state.pageSize);
        if (state.filterAppId) {
            url += "&appId=" + encodeURIComponent(state.filterAppId);
        }
        requestJson(url, {method: "GET"}).then(function (body) {
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
            var errorCell = cell("扫描任务列表加载失败", "system-table-state");
            errorCell.colSpan = 12;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "扫描任务列表加载失败。", "error");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function applicationLabel(application) {
        var name = application.appName || application.appCode || application.appId;
        return application.appCode && application.appCode !== name ? name + "（" + application.appCode + "）" : name;
    }

    function selectedApplication() {
        return state.applications.find(function (application) {
            return application.appId === fields.application.value;
        });
    }

    function loadApplications() {
        return requestJson(config.applicationsUrl + "?pageNo=1&pageSize=100", {method: "GET"}).then(function (body) {
            state.applications = body.data.records || [];
            fields.application.textContent = "";
            filterOptions.textContent = "";
            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = "请选择";
            fields.application.appendChild(placeholder);
            state.applications.forEach(function (application) {
                var option = document.createElement("option");
                option.value = application.appId;
                option.textContent = applicationLabel(application);
                fields.application.appendChild(option);
                var filterOption = document.createElement("option");
                filterOption.value = applicationLabel(application);
                filterOptions.appendChild(filterOption);
            });
        }).catch(function (error) {
            showFeedback(error.message || "应用列表加载失败。", "error");
        });
    }

    function resetVersionOptions(message) {
        fields.version.textContent = "";
        var option = document.createElement("option");
        option.value = "";
        option.textContent = "请选择";
        fields.version.appendChild(option);
        fields.versionHelp.textContent = message || "";
    }

    function loadVersions(appId) {
        resetVersionOptions("正在加载版本...");
        fields.version.disabled = true;
        var url = config.packageVersionsUrl.replace("__APP_ID__", encodeURIComponent(appId));
        requestJson(url, {method: "GET"}).then(function (body) {
            (body.data || []).forEach(function (version) {
                var option = document.createElement("option");
                option.value = version;
                option.textContent = version;
                fields.version.appendChild(option);
            });
            fields.versionHelp.textContent = body.data && body.data.length ? "" : "暂无可选版本";
        }).catch(function (error) {
            resetVersionOptions(error.message || "版本加载失败。");
        }).finally(function () {
            fields.version.disabled = false;
        });
    }

    function updateCreateFields() {
        var application = selectedApplication();
        var hasApplication = Boolean(application);
        var maven = hasApplication && application.packageSourceType === "MAVEN_REPO";
        fields.mavenFields.hidden = !maven;
        fields.uploadFields.hidden = maven;
        fields.version.required = maven;
        fields.file.required = hasApplication && !maven;
        fields.file.value = "";
        fields.fileName.textContent = "未选择文件";
        if (maven) {
            loadVersions(application.appId);
        } else {
            resetVersionOptions("");
        }
    }

    function openDrawer() {
        form.reset();
        fields.mavenFields.hidden = true;
        fields.uploadFields.hidden = false;
        fields.file.required = false;
        fields.version.required = false;
        fields.fileName.textContent = "未选择文件";
        resetVersionOptions("");
        backdrop.hidden = false;
        drawer.classList.add("open");
        drawer.setAttribute("aria-hidden", "false");
        document.body.classList.add("system-drawer-open");
        window.setTimeout(function () { fields.application.focus(); }, 50);
    }

    function closeDrawer() {
        drawer.classList.remove("open");
        drawer.setAttribute("aria-hidden", "true");
        backdrop.hidden = true;
        document.body.classList.remove("system-drawer-open");
    }

    function submitTask(event) {
        event.preventDefault();
        if (!form.reportValidity()) {
            return;
        }
        var application = selectedApplication();
        if (!application) {
            showFeedback("请选择应用。", "error");
            return;
        }
        var maven = application.packageSourceType === "MAVEN_REPO";
        var options;
        if (maven) {
            options = {
                method: "POST",
                body: JSON.stringify({
                    appId: application.appId,
                    packageSourceType: application.packageSourceType,
                    selectedVersion: fields.version.value,
                    packageName: fields.packageName.value.trim()
                })
            };
        } else {
            var file = fields.file.files[0];
            if (!file || !file.name.toLowerCase().endsWith(".gz")) {
                showFeedback("请选择一个 .gz 文件。", "error");
                return;
            }
            var formData = new FormData();
            formData.append("appId", application.appId);
            formData.append("packageName", file.name);
            formData.append("file", file, file.name);
            options = {method: "POST", body: formData};
        }

        submitButton.disabled = true;
        submitButton.textContent = "提交中...";
        requestJson(maven ? config.scanTasksUrl : config.uploadUrl, options).then(function () {
            closeDrawer();
            showFeedback("扫描任务创建成功。", "success");
            loadTasks(1, true);
        }).catch(function (error) {
            showFeedback(error.message || "扫描任务创建失败。", "error");
        }).finally(function () {
            submitButton.disabled = false;
            submitButton.textContent = "确定";
        });
    }

    function runTask(record) {
        state.runningTaskId = record.scanTaskId;
        renderRows();
        requestJson(config.scanTasksUrl + "/" + encodeURIComponent(record.scanTaskId) + "/run", {method: "POST"}).then(function () {
            showFeedback("扫描任务已开始执行。", "success");
            loadTasks(state.pageNo, true);
        }).catch(function (error) {
            showFeedback(error.message || "扫描任务执行失败。", "error");
        }).finally(function () {
            state.runningTaskId = "";
            renderRows();
        });
    }

    function deleteTask(record) {
        if (!window.confirm("确定删除该扫描任务吗？")) {
            return;
        }
        requestJson(config.scanTasksUrl + "/" + encodeURIComponent(record.scanTaskId), {method: "DELETE"}).then(function () {
            showFeedback("扫描任务删除成功。", "success");
            var targetPage = state.records.length === 1 && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadTasks(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "扫描任务删除失败。", "error");
        });
    }

    function applyApplicationFilter() {
        var value = filterInput.value.trim();
        filterClearButton.hidden = !value;
        var match = state.applications.find(function (application) {
            return applicationLabel(application) === value;
        });
        if (!value) {
            state.filterAppId = "";
            loadTasks(1);
        } else if (match) {
            state.filterAppId = match.appId;
            loadTasks(1);
        }
    }

    document.getElementById("scan-task-create-button").addEventListener("click", openDrawer);
    document.getElementById("scan-task-drawer-close").addEventListener("click", closeDrawer);
    document.getElementById("scan-task-form-cancel").addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
    form.addEventListener("submit", submitTask);
    fields.application.addEventListener("change", updateCreateFields);
    fields.file.addEventListener("change", function () {
        fields.fileName.textContent = fields.file.files.length ? fields.file.files[0].name : "未选择文件";
    });
    filterInput.addEventListener("change", applyApplicationFilter);
    filterInput.addEventListener("input", function () {
        filterClearButton.hidden = !filterInput.value.trim();
        if (!filterInput.value) {
            applyApplicationFilter();
            return;
        }
        var exactMatch = state.applications.some(function (application) {
            return applicationLabel(application) === filterInput.value.trim();
        });
        if (exactMatch) {
            applyApplicationFilter();
        }
    });
    filterInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applyApplicationFilter();
        }
    });
    filterClearButton.addEventListener("click", function () {
        filterInput.value = "";
        filterClearButton.hidden = true;
        applyApplicationFilter();
        filterInput.focus();
    });
    prevButton.addEventListener("click", function () { loadTasks(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadTasks(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadTasks(1);
    });
    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadTasks(target);
    }
    pageJumpElement.addEventListener("change", jumpToPage);
    pageJumpElement.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            jumpToPage();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && drawer.classList.contains("open")) {
            closeDrawer();
        }
    });

    loadTasks(1);
    loadApplications();
}());
