(function () {
    "use strict";

    var config = window.interfaceAuditLogsConfig || {};
    var initialFilters = config.initialFilters || {};
    var actionLabels = {
        ASSET_CONFIRM: "资产确认",
        SCAN_TASK_DELETE: "扫描任务删除",
        APPLICATION_DELETE: "应用删除",
        SYSTEM_DELETE: "系统删除",
        SNAPSHOT_DELETE: "快照删除"
    };
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        selectedIds: new Set(),
        actionType: String(initialFilters.actionType || ""),
        operator: String(initialFilters.operator || ""),
        startTime: String(initialFilters.startTime || ""),
        endTime: String(initialFilters.endTime || ""),
        loading: false,
        deleting: false
    };

    var tableBody = document.getElementById("audit-log-table-body");
    var totalElement = document.getElementById("audit-log-total");
    var currentPageElement = document.getElementById("audit-log-current-page");
    var pageSizeElement = document.getElementById("audit-log-page-size");
    var pageJumpElement = document.getElementById("audit-log-page-jump");
    var prevButton = document.getElementById("audit-log-prev-page");
    var nextButton = document.getElementById("audit-log-next-page");
    var feedback = document.getElementById("audit-log-feedback");
    var filterForm = document.getElementById("audit-log-filter-form");
    var actionTypeInput = document.getElementById("audit-log-action-type");
    var operatorInput = document.getElementById("audit-log-operator");
    var startTimeInput = document.getElementById("audit-log-start-time");
    var endTimeInput = document.getElementById("audit-log-end-time");
    var selectAllInput = document.getElementById("audit-log-select-all");
    var batchDeleteButton = document.getElementById("audit-log-delete-batch");
    var confirmBackdrop = document.getElementById("audit-log-confirm-backdrop");
    var confirmMessage = document.getElementById("audit-log-confirm-message");
    var confirmCancel = document.getElementById("audit-log-confirm-cancel");
    var confirmSubmit = document.getElementById("audit-log-confirm-submit");
    var pendingDelete = null;

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

    function jsonRequest(url, method, body) {
        return requestJson(url, {
            method: method,
            headers: {"Content-Type": "application/json"},
            body: body === undefined ? undefined : JSON.stringify(body)
        });
    }

    function showFeedback(message, type) {
        feedback.className = "alert system-feedback " + (type === "success" ? "alert-success" : "alert-danger");
        feedback.textContent = message;
        feedback.hidden = false;
    }

    function cell(text) {
        var element = document.createElement("td");
        var value = String(text === undefined || text === null ? "" : text);
        element.textContent = value || "--";
        element.title = value;
        return element;
    }

    function displayTime(value) {
        return String(value || "").replace("T", " ");
    }

    function updateSelectionControls() {
        var selectableIds = state.records.map(function (record) {
            return record.logId;
        }).filter(Boolean);
        var selectedCount = selectableIds.filter(function (logId) {
            return state.selectedIds.has(logId);
        }).length;
        selectAllInput.checked = selectableIds.length > 0 && selectedCount === selectableIds.length;
        selectAllInput.indeterminate = selectedCount > 0 && selectedCount < selectableIds.length;
        selectAllInput.disabled = state.loading || state.deleting || selectableIds.length === 0;
        batchDeleteButton.disabled = state.loading || state.deleting || state.selectedIds.size === 0;
        batchDeleteButton.title = state.selectedIds.size ? "删除已选择的 " + state.selectedIds.size + " 条记录" : "请先选择记录";
    }

    function selectionCell(record) {
        var element = document.createElement("td");
        var input = document.createElement("input");
        input.type = "checkbox";
        input.setAttribute("aria-label", "选择审计记录");
        input.checked = state.selectedIds.has(record.logId);
        input.disabled = !record.logId || state.deleting;
        input.addEventListener("change", function () {
            if (input.checked) {
                state.selectedIds.add(record.logId);
            } else {
                state.selectedIds.delete(record.logId);
            }
            updateSelectionControls();
        });
        element.appendChild(input);
        return element;
    }

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无审计问题数据");
            emptyCell.className = "system-table-state";
            emptyCell.colSpan = 6;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            updateSelectionControls();
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(selectionCell(record));
            row.appendChild(cell(actionLabels[record.actionType] || record.actionType));
            row.appendChild(cell(record.targetName));
            row.appendChild(cell(record.operator));
            row.appendChild(cell(displayTime(record.createdAt)));

            var actions = document.createElement("td");
            var deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.className = "system-table-action delete";
            deleteButton.textContent = "删除";
            deleteButton.disabled = !record.logId || state.deleting;
            deleteButton.addEventListener("click", function () {
                confirmDeletion("确定删除这条审计记录吗？删除后无法恢复。", function () {
                    return deleteSingle(record.logId);
                });
            });
            actions.appendChild(deleteButton);
            row.appendChild(actions);
            tableBody.appendChild(row);
        });
        updateSelectionControls();
    }

    function renderPagination() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        totalElement.textContent = state.total.toLocaleString();
        currentPageElement.textContent = state.pageNo;
        pageJumpElement.value = state.pageNo;
        pageJumpElement.max = pageCount;
        prevButton.disabled = state.loading || state.deleting || state.pageNo <= 1;
        nextButton.disabled = state.loading || state.deleting || state.pageNo >= pageCount;
        updateSelectionControls();
    }

    function listPayload() {
        return {
            pageNo: state.pageNo,
            pageSize: state.pageSize,
            actionType: state.actionType,
            operator: state.operator,
            startTime: state.startTime,
            endTime: state.endTime
        };
    }

    function loadLogs(pageNo, preserveFeedback) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        state.selectedIds.clear();
        if (!preserveFeedback) {
            feedback.hidden = true;
        }
        renderPagination();
        tableBody.textContent = "";
        var loadingRow = document.createElement("tr");
        var loadingCell = cell("正在加载...");
        loadingCell.className = "system-table-state";
        loadingCell.colSpan = 6;
        loadingRow.appendChild(loadingCell);
        tableBody.appendChild(loadingRow);

        jsonRequest(config.logsUrl, "POST", listPayload()).then(function (body) {
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
            var errorCell = cell("审计问题列表加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 6;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "审计问题列表加载失败。", "error");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function closeConfirmation() {
        confirmBackdrop.hidden = true;
        confirmSubmit.disabled = false;
        confirmCancel.disabled = false;
        pendingDelete = null;
    }

    function confirmDeletion(message, operation) {
        pendingDelete = operation;
        confirmMessage.textContent = message;
        confirmBackdrop.hidden = false;
        confirmSubmit.focus();
    }

    function performPendingDelete() {
        if (!pendingDelete || state.deleting) {
            return;
        }
        state.deleting = true;
        confirmSubmit.disabled = true;
        confirmCancel.disabled = true;
        confirmSubmit.textContent = "删除中...";
        pendingDelete().finally(function () {
            state.deleting = false;
            confirmSubmit.textContent = "确认删除";
            closeConfirmation();
            renderPagination();
        });
    }

    function deleteSingle(logId) {
        var url = config.logUrlTemplate.replace("__LOG_ID__", encodeURIComponent(logId));
        return jsonRequest(url, "DELETE").then(function () {
            showFeedback("审计记录删除成功。", "success");
            var targetPage = state.records.length === 1 && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadLogs(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "审计记录删除失败。", "error");
        });
    }

    function deleteSelected() {
        var logIds = Array.from(state.selectedIds);
        return jsonRequest(config.batchDeleteUrl, "POST", {logIds: logIds}).then(function () {
            showFeedback("已删除选中的 " + logIds.length + " 条审计记录。", "success");
            var targetPage = logIds.length >= state.records.length && state.pageNo > 1 ? state.pageNo - 1 : state.pageNo;
            loadLogs(targetPage, true);
        }).catch(function (error) {
            showFeedback(error.message || "批量删除审计记录失败。", "error");
        });
    }

    function applyFilters() {
        var startTime = startTimeInput.value;
        var endTime = endTimeInput.value;
        if (startTime && endTime && startTime > endTime) {
            showFeedback("开始时间不能晚于结束时间。", "error");
            return;
        }
        state.actionType = actionTypeInput.value;
        state.operator = operatorInput.value.trim();
        state.startTime = startTime;
        state.endTime = endTime;
        loadLogs(1);
    }

    filterForm.addEventListener("submit", function (event) {
        event.preventDefault();
        applyFilters();
    });
    actionTypeInput.addEventListener("change", applyFilters);
    startTimeInput.addEventListener("change", applyFilters);
    endTimeInput.addEventListener("change", applyFilters);
    operatorInput.addEventListener("input", function () {
        if (!operatorInput.value && state.operator) {
            applyFilters();
        }
    });
    selectAllInput.addEventListener("change", function () {
        state.records.forEach(function (record) {
            if (!record.logId) {
                return;
            }
            if (selectAllInput.checked) {
                state.selectedIds.add(record.logId);
            } else {
                state.selectedIds.delete(record.logId);
            }
        });
        renderRows();
    });
    batchDeleteButton.addEventListener("click", function () {
        if (!state.selectedIds.size) {
            return;
        }
        confirmDeletion("确定删除选中的 " + state.selectedIds.size + " 条审计记录吗？删除后无法恢复。", deleteSelected);
    });
    confirmCancel.addEventListener("click", closeConfirmation);
    confirmSubmit.addEventListener("click", performPendingDelete);
    confirmBackdrop.addEventListener("click", function (event) {
        if (event.target === confirmBackdrop && !state.deleting) {
            closeConfirmation();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !confirmBackdrop.hidden && !state.deleting) {
            closeConfirmation();
        }
    });
    prevButton.addEventListener("click", function () { loadLogs(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadLogs(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadLogs(1);
    });

    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadLogs(target);
    }

    pageJumpElement.addEventListener("change", jumpToPage);
    pageJumpElement.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            jumpToPage();
        }
    });

    actionTypeInput.value = state.actionType;
    operatorInput.value = state.operator;
    startTimeInput.value = state.startTime;
    endTimeInput.value = state.endTime;
    loadLogs(1);
}());
