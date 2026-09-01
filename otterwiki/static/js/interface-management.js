(function () {
    "use strict";

    var config = window.interfaceDashboardConfig || {};
    var list = document.getElementById("recent-activity");
    var error = document.getElementById("dashboard-error");

    function activityRow(item) {
        var row = document.createElement("div");
        row.className = "activity-row";

        [
            ["activity-name", item.applicationName],
            ["activity-version", item.appVersion],
            ["activity-remark", item.remark],
            ["activity-date", item.date]
        ].forEach(function (field) {
            var cell = document.createElement("span");
            cell.className = field[0];
            cell.textContent = field[1] || "--";
            cell.title = field[1] || "";
            row.appendChild(cell);
        });
        return row;
    }

    function render(data) {
        document.querySelectorAll("[data-summary]").forEach(function (element) {
            var value = data[element.dataset.summary];
            element.textContent = typeof value === "number" ? value.toLocaleString() : "--";
        });

        list.textContent = "";
        if (!data.recentActivity || data.recentActivity.length === 0) {
            var empty = document.createElement("div");
            empty.className = "activity-state";
            empty.textContent = "暂无最近扫描或快照记录";
            list.appendChild(empty);
            return;
        }
        data.recentActivity.forEach(function (item) {
            list.appendChild(activityRow(item));
        });
    }

    function showError(message) {
        error.textContent = message;
        error.hidden = false;
        list.textContent = "";
        var state = document.createElement("div");
        state.className = "activity-state";
        state.textContent = "数据加载失败";
        list.appendChild(state);
    }

    fetch(config.summaryUrl, {headers: {"Accept": "application/json"}})
        .then(function (response) {
            return response.json().then(function (body) {
                if (!response.ok || body.code !== 200) {
                    throw new Error(body.msg || "工作台数据加载失败。");
                }
                return body.data;
            });
        })
        .then(render)
        .catch(function (requestError) {
            showError(requestError.message || "工作台数据加载失败。");
        });
}());
