(function () {
    "use strict";

    var config = window.interfaceSystemsConfig || {};
    var state = {pageNo: 1, pageSize: 10, total: 0, records: [], loading: false};
    var tableBody = document.getElementById("system-table-body");
    var totalElement = document.getElementById("system-total");
    var currentPageElement = document.getElementById("system-current-page");
    var pageSizeElement = document.getElementById("system-page-size");
    var pageJumpElement = document.getElementById("system-page-jump");
    var prevButton = document.getElementById("system-prev-page");
    var nextButton = document.getElementById("system-next-page");
    var feedback = document.getElementById("system-feedback");
    var drawer = document.getElementById("system-drawer");
    var backdrop = document.getElementById("system-drawer-backdrop");
    var drawerTitle = document.getElementById("system-drawer-title");
    var form = document.getElementById("system-form");
    var submitButton = document.getElementById("system-form-submit");
    var fields = {
        id: document.getElementById("system-id"),
        code: document.getElementById("system-code"),
        name: document.getElementById("system-name"),
        ownerDept: document.getElementById("system-owner-dept"),
        status: document.getElementById("system-status"),
        remark: document.getElementById("system-remark")
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

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无系统数据", "system-table-state");
            emptyCell.colSpan = 5;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.systemCode));
            row.appendChild(cell(record.systemName));
            row.appendChild(cell(record.ownerDept));

            var statusCell = document.createElement("td");
            var status = document.createElement("span");
            var active = record.status === "ACTIVE";
            status.className = "system-status " + (active ? "active" : "offline");
            status.textContent = active ? "启用" : (record.status === "OFFLINE" ? "停用" : (record.status || "--"));
            statusCell.appendChild(status);
            row.appendChild(statusCell);

            var actions = document.createElement("td");
            actions.appendChild(actionButton("编辑", "edit", function () {
                openDrawer(record);
            }, !record.systemId));
            actions.appendChild(actionButton("删除", "delete", function () {
                deleteSystem(record);
            }, !record.systemId));
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

    function loadSystems(pageNo, preserveFeedback) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        if (!preserveFeedback) {
            feedback.hidden = true;
        }
        renderPagination();
        tableBody.textContent = "";
        var row = document.createElement("tr");
        var loadingCell = cell("正在加载...", "system-table-state");
        loadingCell.colSpan = 5;
        row.appendChild(loadingCell);
        tableBody.appendChild(row);

        var url = config.systemsUrl + "?pageNo=" + encodeURIComponent(state.pageNo) + "&pageSize=" + encodeURIComponent(state.pageSize);
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
            var errorCell = cell("系统列表加载失败", "system-table-state");
            errorCell.colSpan = 5;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "系统列表加载失败。", "error");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function openDrawer(record) {
        record = record || {};
        form.reset();
        fields.id.value = record.systemId || "";
        fields.code.value = record.systemCode || "";
        fields.name.value = record.systemName || "";
        fields.ownerDept.value = record.ownerDept || "";
        fields.status.value = record.status === "OFFLINE" ? "OFFLINE" : "ACTIVE";
        fields.remark.value = record.remark || "";
        drawerTitle.textContent = record.systemId ? "编辑系统" : "新增系统";
        backdrop.hidden = false;
        drawer.classList.add("open");
        drawer.setAttribute("aria-hidden", "false");
        document.body.classList.add("system-drawer-open");
        window.setTimeout(function () { fields.code.focus(); }, 50);
    }

    function closeDrawer() {
        drawer.classList.remove("open");
        drawer.setAttribute("aria-hidden", "true");
        backdrop.hidden = true;
        document.body.classList.remove("system-drawer-open");
    }

    function formPayload() {
        return {
            systemCode: fields.code.value.trim(),
            systemName: fields.name.value.trim(),
            ownerDept: fields.ownerDept.value.trim(),
            status: fields.status.value,
            remark: fields.remark.value.trim()
        };
    }

    function submitSystem(event) {
        event.preventDefault();
        if (!form.reportValidity()) {
            return;
        }
        var id = fields.id.value;
        var editing = Boolean(id);
        var url = editing ? config.systemsUrl + "/" + encodeURIComponent(id) : config.systemsUrl;
        submitButton.disabled = true;
        submitButton.textContent = "提交中...";
        requestJson(url, {
            method: editing ? "PUT" : "POST",
            body: JSON.stringify(formPayload())
        }).then(function () {
            closeDrawer();
            showFeedback(editing ? "系统更新成功。" : "系统创建成功。", "success");
            loadSystems(editing ? state.pageNo : 1, true);
        }).catch(function (error) {
            showFeedback(error.message || "系统保存失败。", "error");
        }).finally(function () {
            submitButton.disabled = false;
            submitButton.textContent = "确定";
        });
    }

    function deleteSystem(record) {
        if (!window.confirm("确定删除系统“" + (record.systemName || record.systemCode) + "”吗？")) {
            return;
        }
        requestJson(config.systemsUrl + "/" + encodeURIComponent(record.systemId), {
            method: "DELETE"
        }).then(function () {
            showFeedback("系统删除成功。", "success");
            var targetPage = state.records.length === 1 && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadSystems(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "系统删除失败。", "error");
        });
    }

    document.getElementById("system-create-button").addEventListener("click", function () { openDrawer(); });
    document.getElementById("system-drawer-close").addEventListener("click", closeDrawer);
    document.getElementById("system-form-cancel").addEventListener("click", closeDrawer);
    backdrop.addEventListener("click", closeDrawer);
    form.addEventListener("submit", submitSystem);
    prevButton.addEventListener("click", function () { loadSystems(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadSystems(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadSystems(1);
    });
    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadSystems(target);
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

    loadSystems(1);
}());
