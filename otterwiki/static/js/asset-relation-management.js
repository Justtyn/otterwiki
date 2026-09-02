(function () {
    "use strict";

    var config = window.interfaceAssetRelationsConfig || {};
    var state = {
        pageNo: 1,
        pageSize: 10,
        total: 0,
        records: [],
        assetCode: String(config.initialAssetCode || ""),
        loading: false
    };
    var tableBody = document.getElementById("asset-relation-table-body");
    var totalElement = document.getElementById("asset-relation-total");
    var currentPageElement = document.getElementById("asset-relation-current-page");
    var pageSizeElement = document.getElementById("asset-relation-page-size");
    var pageJumpElement = document.getElementById("asset-relation-page-jump");
    var prevButton = document.getElementById("asset-relation-prev-page");
    var nextButton = document.getElementById("asset-relation-next-page");
    var feedback = document.getElementById("asset-relation-feedback");
    var assetCodeInput = document.getElementById("asset-relation-code");

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
            var emptyCell = cell("暂无资产关系数据");
            emptyCell.className = "system-table-state";
            emptyCell.colSpan = 6;
            emptyRow.appendChild(emptyCell);
            tableBody.appendChild(emptyRow);
            return;
        }

        state.records.forEach(function (record) {
            var row = document.createElement("tr");
            row.appendChild(cell(record.relType));
            row.appendChild(cell(record.srcAssetCode));
            row.appendChild(cell(record.targetAssetCode));
            row.appendChild(cell(record.seqNo));
            row.appendChild(cell(record.matchRule));
            row.appendChild(cell(record.relAttrsJson));
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
        return config.relationsUrl + "?" + parameters.join("&");
    }

    function loadRelations(pageNo) {
        state.loading = true;
        state.pageNo = Math.max(1, pageNo || 1);
        feedback.hidden = true;
        renderPagination();
        tableBody.textContent = "";
        var loadingRow = document.createElement("tr");
        var loadingCell = cell("正在加载...");
        loadingCell.className = "system-table-state";
        loadingCell.colSpan = 6;
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
            var errorCell = cell("资产关系列表加载失败");
            errorCell.className = "system-table-state";
            errorCell.colSpan = 6;
            errorRow.appendChild(errorCell);
            tableBody.appendChild(errorRow);
            showFeedback(error.message || "资产关系列表加载失败。");
        }).finally(function () {
            state.loading = false;
            renderPagination();
        });
    }

    function applyFilter() {
        state.assetCode = assetCodeInput.value.trim();
        loadRelations(1);
    }

    document.getElementById("asset-relation-search").addEventListener("click", applyFilter);
    assetCodeInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applyFilter();
        }
    });
    assetCodeInput.addEventListener("input", function () {
        if (!assetCodeInput.value && state.assetCode) {
            applyFilter();
        }
    });
    prevButton.addEventListener("click", function () { loadRelations(state.pageNo - 1); });
    nextButton.addEventListener("click", function () { loadRelations(state.pageNo + 1); });
    pageSizeElement.addEventListener("change", function () {
        state.pageSize = Number(pageSizeElement.value) || 10;
        loadRelations(1);
    });

    function jumpToPage() {
        var pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
        var target = Math.min(pageCount, Math.max(1, Number(pageJumpElement.value) || 1));
        loadRelations(target);
    }

    pageJumpElement.addEventListener("change", jumpToPage);
    pageJumpElement.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            jumpToPage();
        }
    });

    assetCodeInput.value = state.assetCode;
    loadRelations(1);
}());
