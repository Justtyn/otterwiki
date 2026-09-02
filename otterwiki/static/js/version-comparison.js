(function () {
    "use strict";

    var config = window.interfaceVersionComparisonConfig || {};
    var state = {
        applications: [],
        snapshots: [],
        appId: String(config.initialAppId || ""),
        initialAppVersion: String(config.initialAppVersion || ""),
        leftSnapshotId: "",
        rightSnapshotId: "",
        requestSequence: 0
    };
    var dimensionLabels = {
        TRADE_API_DEF: "接口定义",
        TRADE_API_FIELD: "接口字段"
    };
    var changeTypeLabels = {
        ADDED: "新增",
        ADD: "新增",
        CREATED: "新增",
        MODIFIED: "修改",
        MODIFY: "修改",
        UPDATED: "修改",
        UPDATE: "修改",
        DELETED: "删除",
        DELETE: "删除",
        REMOVED: "删除"
    };

    var feedback = document.getElementById("version-comparison-feedback");
    var applicationSelect = document.getElementById("version-comparison-application");
    var leftSelect = document.getElementById("version-comparison-left-snapshot");
    var rightSelect = document.getElementById("version-comparison-right-snapshot");
    var results = document.getElementById("version-comparison-results");
    var definitionCount = document.getElementById("version-comparison-definition-count");
    var fieldCount = document.getElementById("version-comparison-field-count");
    var versions = document.getElementById("version-comparison-versions");
    var changeTotal = document.getElementById("version-comparison-change-total");
    var changeList = document.getElementById("version-comparison-change-list");
    var detailBackdrop = document.getElementById("version-comparison-detail-backdrop");
    var detailTitle = document.getElementById("version-comparison-detail-title");
    var detailDescription = document.getElementById("version-comparison-detail-description");
    var beforeList = document.getElementById("version-comparison-before");
    var afterList = document.getElementById("version-comparison-after");
    var detailClose = document.getElementById("version-comparison-detail-close");

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

    function clearFeedback() {
        feedback.hidden = true;
    }

    function applicationLabel(application) {
        var name = application.appName || application.appCode || application.appId;
        if (application.appCode && application.appCode !== name) {
            return name + "（" + application.appCode + "）";
        }
        return name;
    }

    function option(select, value, label) {
        var element = document.createElement("option");
        element.value = value;
        element.textContent = label;
        select.appendChild(element);
        return element;
    }

    function fillApplications() {
        applicationSelect.textContent = "";
        option(applicationSelect, "", "请选择");
        state.applications.forEach(function (application) {
            option(applicationSelect, application.appId, applicationLabel(application));
        });
        applicationSelect.value = state.appId;
    }

    function fillSnapshotSelect(select, selectedValue, otherValue) {
        select.textContent = "";
        option(select, "", "请选择");
        state.snapshots.forEach(function (snapshot) {
            var snapshotOption = option(select, snapshot.snapshotId, snapshot.label || snapshot.snapshotId);
            snapshotOption.disabled = snapshot.snapshotId === otherValue && snapshot.snapshotId !== selectedValue;
            if (snapshot.current) {
                snapshotOption.textContent += "（当前）";
            }
        });
        select.value = selectedValue;
        select.disabled = !state.snapshots.length;
    }

    function renderSnapshotOptions() {
        fillSnapshotSelect(leftSelect, state.leftSnapshotId, state.rightSnapshotId);
        fillSnapshotSelect(rightSelect, state.rightSnapshotId, state.leftSnapshotId);
    }

    function setSnapshotLoading(loading) {
        leftSelect.disabled = loading || !state.snapshots.length;
        rightSelect.disabled = loading || !state.snapshots.length;
        if (loading) {
            leftSelect.textContent = "";
            rightSelect.textContent = "";
            option(leftSelect, "", "正在加载...");
            option(rightSelect, "", "正在加载...");
        }
    }

    function loadApplications() {
        applicationSelect.disabled = true;
        return requestJson(config.applicationsUrl + "?pageNo=1&pageSize=100").then(function (body) {
            state.applications = body.data.records || [];
            if (state.appId && !state.applications.some(function (application) {
                return application.appId === state.appId;
            })) {
                state.appId = "";
            }
            fillApplications();
            if (state.appId) {
                return loadSnapshotOptions();
            }
        }).catch(function (error) {
            showFeedback(error.message || "应用列表加载失败。");
        }).finally(function () {
            applicationSelect.disabled = false;
        });
    }

    function loadSnapshotOptions() {
        state.snapshots = [];
        state.leftSnapshotId = "";
        state.rightSnapshotId = "";
        results.hidden = true;
        if (!state.appId) {
            renderSnapshotOptions();
            return Promise.resolve();
        }
        clearFeedback();
        setSnapshotLoading(true);
        var url = config.snapshotOptionsUrlTemplate.replace("__APP_ID__", encodeURIComponent(state.appId));
        return requestJson(url).then(function (body) {
            state.snapshots = body.data || [];
            if (state.initialAppVersion) {
                var initialSnapshot = state.snapshots.find(function (snapshot) {
                    return snapshot.appVersion === state.initialAppVersion;
                });
                if (initialSnapshot) {
                    state.leftSnapshotId = initialSnapshot.snapshotId;
                }
                state.initialAppVersion = "";
            }
            renderSnapshotOptions();
            if (state.snapshots.length < 2) {
                showFeedback("该应用至少需要两个快照才能进行版本对比。");
            }
        }).catch(function (error) {
            renderSnapshotOptions();
            showFeedback(error.message || "快照选项加载失败。");
        }).finally(function () {
            setSnapshotLoading(false);
        });
    }

    function typeClass(changeType) {
        var value = String(changeType || "").toUpperCase();
        if (["ADDED", "ADD", "CREATED"].indexOf(value) !== -1) {
            return "added";
        }
        if (["DELETED", "DELETE", "REMOVED"].indexOf(value) !== -1) {
            return "deleted";
        }
        return "modified";
    }

    function displayChangeType(changeType) {
        var value = String(changeType || "").toUpperCase();
        return changeTypeLabels[value] || changeType || "变更";
    }

    function renderChanges(changes) {
        changeList.textContent = "";
        changeTotal.textContent = "共 " + changes.length + " 项";
        if (!changes.length) {
            var empty = document.createElement("div");
            empty.className = "version-comparison-empty";
            empty.textContent = "所选版本之间暂无差异";
            changeList.appendChild(empty);
            return;
        }

        changes.forEach(function (change) {
            var item = document.createElement("button");
            item.type = "button";
            item.className = "version-comparison-change-item";

            var meta = document.createElement("span");
            meta.className = "version-comparison-change-meta";
            var type = document.createElement("span");
            type.className = "version-comparison-change-type " + typeClass(change.changeType);
            type.textContent = displayChangeType(change.changeType);
            var dimension = document.createElement("span");
            dimension.className = "version-comparison-dimension";
            dimension.textContent = dimensionLabels[change.dimension] || change.dimension || "其他差异";
            meta.appendChild(type);
            meta.appendChild(dimension);

            var copy = document.createElement("span");
            copy.className = "version-comparison-change-copy";
            var title = document.createElement("strong");
            title.textContent = change.changeDescription || change.key || "差异项";
            var key = document.createElement("small");
            key.textContent = change.key ? "标识：" + change.key : "点击查看修改前后详情";
            copy.appendChild(title);
            copy.appendChild(key);

            var arrow = document.createElement("i");
            arrow.className = "fas fa-chevron-right";
            arrow.setAttribute("aria-hidden", "true");
            item.appendChild(meta);
            item.appendChild(copy);
            item.appendChild(arrow);
            item.addEventListener("click", function () {
                openDetail(change);
            });
            changeList.appendChild(item);
        });
    }

    function snapshotLabel(snapshotId, fallbackVersion) {
        var snapshot = state.snapshots.find(function (item) {
            return item.snapshotId === snapshotId;
        });
        return snapshot ? snapshot.label : (fallbackVersion || snapshotId);
    }

    function renderDiff(data) {
        var summary = data.summary || {};
        var changes = data.changes || [];
        definitionCount.textContent = Number(summary.tradeApiDefChangeCount || 0).toLocaleString();
        fieldCount.textContent = Number(summary.tradeApiFieldChangeCount || 0).toLocaleString();
        versions.textContent = snapshotLabel(data.leftSnapshotId || state.leftSnapshotId, data.leftVersion) + "  →  " + snapshotLabel(data.rightSnapshotId || state.rightSnapshotId, data.rightVersion);
        renderChanges(changes);
        results.hidden = false;
    }

    function loadDiff() {
        if (!state.leftSnapshotId || !state.rightSnapshotId) {
            results.hidden = true;
            return;
        }
        if (state.leftSnapshotId === state.rightSnapshotId) {
            results.hidden = true;
            showFeedback("左右快照不能相同。");
            return;
        }
        clearFeedback();
        var requestSequence = ++state.requestSequence;
        results.hidden = false;
        changeList.textContent = "";
        var loading = document.createElement("div");
        loading.className = "version-comparison-empty";
        loading.textContent = "正在计算版本差异...";
        changeList.appendChild(loading);
        var url = config.snapshotDiffsUrl + "?leftSnapshotId=" + encodeURIComponent(state.leftSnapshotId) + "&rightSnapshotId=" + encodeURIComponent(state.rightSnapshotId);
        requestJson(url).then(function (body) {
            if (requestSequence !== state.requestSequence) {
                return;
            }
            renderDiff(body.data || {});
        }).catch(function (error) {
            if (requestSequence !== state.requestSequence) {
                return;
            }
            results.hidden = true;
            showFeedback(error.message || "版本差异加载失败。");
        });
    }

    function valueText(value) {
        if (value === true) {
            return "是";
        }
        if (value === false) {
            return "否";
        }
        if (value === undefined || value === null || value === "") {
            return "--";
        }
        return String(value);
    }

    function sideFields(dimension) {
        if (dimension === "TRADE_API_DEF") {
            return [
                ["接口名称", "interfaceName"],
                ["接口类型", "interfaceType"],
                ["XML 文件路径", "xmlPath"]
            ];
        }
        if (dimension === "TRADE_API_FIELD") {
            return [
                ["字段名称", "fieldName"],
                ["字段类型", "fieldType"],
                ["是否必输", "required"],
                ["是否多值", "multiple"],
                ["是否数组", "array"]
            ];
        }
        return [["数据", "value"]];
    }

    function renderSide(list, data, dimension) {
        list.textContent = "";
        if (!data) {
            var emptyTerm = document.createElement("dt");
            emptyTerm.textContent = "数据";
            var emptyValue = document.createElement("dd");
            emptyValue.textContent = "无";
            list.appendChild(emptyTerm);
            list.appendChild(emptyValue);
            return;
        }
        sideFields(dimension).forEach(function (field) {
            var term = document.createElement("dt");
            term.textContent = field[0];
            var description = document.createElement("dd");
            description.textContent = valueText(data[field[1]]);
            description.title = description.textContent;
            list.appendChild(term);
            list.appendChild(description);
        });
    }

    function openDetail(change) {
        detailTitle.textContent = (dimensionLabels[change.dimension] || "差异") + "详情";
        detailDescription.textContent = change.changeDescription || change.key || "查看修改前后的字段值";
        renderSide(beforeList, change.before, change.dimension);
        renderSide(afterList, change.after, change.dimension);
        detailBackdrop.hidden = false;
        detailClose.focus();
    }

    function closeDetail() {
        detailBackdrop.hidden = true;
    }

    applicationSelect.addEventListener("change", function () {
        state.appId = applicationSelect.value;
        state.initialAppVersion = "";
        loadSnapshotOptions();
    });
    leftSelect.addEventListener("change", function () {
        state.leftSnapshotId = leftSelect.value;
        renderSnapshotOptions();
        loadDiff();
    });
    rightSelect.addEventListener("change", function () {
        state.rightSnapshotId = rightSelect.value;
        renderSnapshotOptions();
        loadDiff();
    });
    detailClose.addEventListener("click", closeDetail);
    detailBackdrop.addEventListener("click", function (event) {
        if (event.target === detailBackdrop) {
            closeDetail();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !detailBackdrop.hidden) {
            closeDetail();
        }
    });

    renderSnapshotOptions();
    loadApplications();
}());
