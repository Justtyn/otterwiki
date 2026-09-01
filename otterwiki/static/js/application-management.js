(function () {
    "use strict";

    var config = window.interfaceApplicationsConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        systems: [],
        loading: false
    };
    var tableBody = document.getElementById("application-table-body");
    var totalElement = document.getElementById("application-total");
    var currentPageElement = document.getElementById("application-current-page");
    var pageSizeElement = document.getElementById("application-page-size");
    var pageJumpElement = document.getElementById("application-page-jump");
    var prevButton = document.getElementById("application-prev-page");
    var nextButton = document.getElementById("application-next-page");
    var feedback = document.getElementById("application-feedback");
    var drawer = document.getElementById("application-drawer");
    var backdrop = document.getElementById("application-drawer-backdrop");
    var drawerTitle = document.getElementById("application-drawer-title");
    var form = document.getElementById("application-form");
    var submitButton = document.getElementById("application-form-submit");
    var fields = {
        id: document.getElementById("application-id"),
        systemId: document.getElementById("application-system-id"),
        code: document.getElementById("application-code"),
        name: document.getElementById("application-name"),
        sourceType: document.getElementById("application-package-source"),
        packageRule: document.getElementById("application-package-rule"),
        packagePath: document.getElementById("application-package-path"),
        packageGroup: document.getElementById("application-package-group"),
        packageArtifact: document.getElementById("application-package-artifact"),
        remark: document.getElementById("application-remark")
    };

    function requestJson(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        options.headers.Accept = "application/json";
        if (options.method && options.method !== "GET") {
            options.headers["Content-Type"] = "application/json";
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

    function cell(text, className) {
        var element = document.createElement("td");
        element.textContent = text || "--";
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

    function systemName(record) {
        if (record.systemName) {
            return record.systemName;
        }
        var match = state.systems.find(function (system) {
            return system.systemId === record.systemId;
        });
        return match ? match.systemName : record.systemId;
    }

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无应用数据", "system-table-state");
            emptyCell.colSpan = 4;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(systemName(record)));
            row.appendChild(cell(record.appCode));
            row.appendChild(cell(record.appName));
            var actions = document.createElement("td");
            actions.appendChild(actionButton("编辑", "edit", function () {
                openDrawer(record);
            }, !record.appId));
            actions.appendChild(actionButton("删除", "delete", function () {
                deleteApplication(record);
            }, !record.appId));
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

    function loadApplications(pageNo, preserveFeedback) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        if (!preserveFeedback) {
            feedback.hidden = true;
        }
        renderPagination();
        tableBody.textContent = "";
        var row = document.createElement("tr");
        var loadingCell = cell("正在加载...", "system-table-state");
        loadingCell.colSpan = 4;
        row.appendChild(loadingCell);
        tableBody.appendChild(row);

        var url = config.applicationsUrl + "?pageNo=" + encodeURIComponent(state.pageNo) + "&pageSize=" + encodeURIComponent(state.pageSize);
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
            var errorCell = cell("应用列表加载失败", "system-table-state");
            errorCell.colSpan = 4;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "应用列表加载失败。", "error");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function appendSystemOption(system) {
        var option = document.createElement("option");
        option.value = system.systemId;
        option.textContent = system.systemName || system.systemCode || system.systemId;
        fields.systemId.appendChild(option);
    }

    function loadSystemOptions() {
        return requestJson(config.systemsUrl + "?pageNo=1&pageSize=100", {method: "GET"}).then(function (body) {
            state.systems = body.data.records || [];
            fields.systemId.textContent = "";
            var placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = "请选择";
            fields.systemId.appendChild(placeholder);
            state.systems.forEach(appendSystemOption);
            renderRows();
        }).catch(function (error) {
            showFeedback(error.message || "所属系统列表加载失败。", "error");
        });
    }

    function updateSourceFields() {
        var maven = fields.sourceType.value === "MAVEN_REPO";
        document.querySelectorAll(".application-local-field").forEach(function (element) {
            element.hidden = maven;
        });
        document.querySelectorAll(".application-maven-field").forEach(function (element) {
            element.hidden = !maven;
        });
    }

    function ensureSelectedSystem(record) {
        var exists = Array.prototype.some.call(fields.systemId.options, function (option) {
            return option.value === record.systemId;
        });
        if (!record.systemId || exists) {
            return;
        }
        appendSystemOption({
            systemId: record.systemId,
            systemName: record.systemName || record.systemId
        });
    }

    function openDrawer(record) {
        record = record || {};
        form.reset();
        ensureSelectedSystem(record);
        fields.id.value = record.appId || "";
        fields.systemId.value = record.systemId || "";
        fields.code.value = record.appCode || "";
        fields.name.value = record.appName || "";
        fields.sourceType.value = record.packageSourceType || "";
        fields.packageRule.value = record.packageNameRule || "";
        fields.packagePath.value = record.packagePath || "";
        fields.packageGroup.value = record.packageGroupId || "";
        fields.packageArtifact.value = record.packageArtifactId || "";
        fields.remark.value = record.remark || "";
        updateSourceFields();
        drawerTitle.textContent = record.appId ? "编辑应用" : "新增应用";
        backdrop.hidden = false;
        drawer.classList.add("open");
        drawer.setAttribute("aria-hidden", "false");
        document.body.classList.add("system-drawer-open");
        window.setTimeout(function () { fields.systemId.focus(); }, 50);
    }

    function closeDrawer() {
        drawer.classList.remove("open");
        drawer.setAttribute("aria-hidden", "true");
        backdrop.hidden = true;
        document.body.classList.remove("system-drawer-open");
    }

    function formPayload() {
        return {
            systemId: fields.systemId.value,
            appCode: fields.code.value.trim(),
            appName: fields.name.value.trim(),
            packageSourceType: fields.sourceType.value,
            packageNameRule: fields.packageRule.value.trim(),
            packagePath: fields.packagePath.value.trim(),
            packageGroupId: fields.packageGroup.value.trim(),
            packageArtifactId: fields.packageArtifact.value.trim(),
            remark: fields.remark.value.trim()
        };
    }

    function submitApplication(event) {
        event.preventDefault();
        if (!form.reportValidity()) {
            return;
        }
        var id = fields.id.value;
        var editing = Boolean(id);
        var url = editing ? config.applicationsUrl + "/" + encodeURIComponent(id) : config.applicationsUrl;
        submitButton.disabled = true;
        submitButton.textContent = "提交中...";
        requestJson(url, {
            method: editing ? "PUT" : "POST",
            body: JSON.stringify(formPayload())
        }).then(function () {
            closeDrawer();
            showFeedback(editing ? "应用更新成功。" : "应用创建成功。", "success");
            loadApplications(editing ? state.pageNo : 1, true);
        }).catch(function (error) {
            showFeedback(error.message || "应用保存失败。", "error");
        }).finally(function () {
            submitButton.disabled = false;
            submitButton.textContent = "确定";
        });
    }

    function deleteApplication(record) {
        if (!window.confirm("确定删除应用“" + (record.appName || record.appCode) + "”吗？")) {
            return;
        }
        requestJson(config.applicationsUrl + "/" + encodeURIComponent(record.appId), {
            method: "DELETE"
        }).then(function () {
            showFeedback("应用删除成功。", "success");
            var targetPage = state.records.length === 1 && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadApplications(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "应用删除失败。", "error");
        });
    }

    document.getElementById("application-create-button").addEventListener("click", function () { openDrawer(); });
    document.getElementById("application-drawer-close").addEventListener("click", closeDrawer);
    document.getElementById("application-form-cancel").addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
    form.addEventListener("submit", submitApplication);
    fields.sourceType.addEventListener("change", updateSourceFields);
    prevButton.addEventListener("click", function () { loadApplications(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadApplications(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadApplications(1);
    });
    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadApplications(target);
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

    loadApplications(1);
    loadSystemOptions();
}());
