(function () {
    "use strict";

    var config = window.interfaceApiGatewayConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        assetCode: String(config.initialAssetCode || ""),
        assetName: String(config.initialAssetName || ""),
        status: String(config.initialStatus || ""),
        loading: false
    };
    var statusLabels = {
        published: "已发布",
        PUBLISHED: "已发布",
        downline: "已下线",
        DOWNLINE: "已下线",
        waitTest: "待测试",
        WAIT_TEST: "待测试",
        waitPublish: "待发布",
        WAIT_PUBLISH: "待发布"
    };
    var tableBody = document.getElementById("api-gateway-table-body");
    var totalElement = document.getElementById("api-gateway-total");
    var currentPageElement = document.getElementById("api-gateway-current-page");
    var pageSizeElement = document.getElementById("api-gateway-page-size");
    var pageJumpElement = document.getElementById("api-gateway-page-jump");
    var prevButton = document.getElementById("api-gateway-prev-page");
    var nextButton = document.getElementById("api-gateway-next-page");
    var feedback = document.getElementById("api-gateway-feedback");
    var assetCodeInput = document.getElementById("api-gateway-asset-code");
    var assetNameInput = document.getElementById("api-gateway-asset-name");
    var statusSelect = document.getElementById("api-gateway-status");

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

    function renderRows() {
        tableBody.textContent = "";
        if (!state.records.length) {
            var emptyRow = document.createElement("tr");
            var emptyCell = cell("暂无 API 网关资产数据");
            emptyCell.className = "system-table-state";
            emptyCell.colSpan = 3;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.assetCode));
            row.appendChild(cell(record.assetName));
            row.appendChild(cell(statusLabels[record.status] || record.status));
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
        if (state.assetCode) {
            parameters.push("assetCode=" + encodeURIComponent(state.assetCode));
        }
        if (state.assetName) {
            parameters.push("assetName=" + encodeURIComponent(state.assetName));
        }
        if (state.status) {
            parameters.push("status=" + encodeURIComponent(state.status));
        }
        return config.assetsUrl + "?" + parameters.join("&");
    }

    function loadAssets(pageNo) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        feedback.hidden = true;
        renderPagination();
        tableBody.textContent = "";
        var loadingRow = document.createElement("tr");
        var loadingCell = cell("正在加载...");
        loadingCell.className = "system-table-state";
        loadingCell.colSpan = 3;
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
            var errorCell = cell("API 网关资产列表加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 3;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "API 网关资产列表加载失败。");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function applyFilters() {
        state.assetCode = assetCodeInput.value.trim();
        state.assetName = assetNameInput.value.trim();
        state.status = statusSelect.value;
        loadAssets(1);
    }

    document.getElementById("api-gateway-search").addEventListener("click", applyFilters);
    [assetCodeInput, assetNameInput].forEach(function (input) {
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                applyFilters();
            }
        });
        input.addEventListener("input", function () {
            if (!input.value && (state.assetCode || state.assetName)) {
                applyFilters();
            }
        });
    });
    statusSelect.addEventListener("change", applyFilters);
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

    assetCodeInput.value = state.assetCode;
    assetNameInput.value = state.assetName;
    statusSelect.value = state.status;
    loadAssets(1);
}());
