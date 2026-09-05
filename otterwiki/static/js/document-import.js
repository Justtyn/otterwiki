/* 文档上传与持久化任务轮询。网络错误不代表导入失败。 */
(function () {
    "use strict";
    const form = document.getElementById("document-import-form");
    if (!form) return;
    const panel = document.getElementById("document-import-progress");
    const phase = document.getElementById("document-import-phase");
    const message = document.getElementById("document-import-message");
    const counts = document.getElementById("document-import-counts");
    const elapsed = document.getElementById("document-import-elapsed");
    const bar = document.getElementById("document-import-bar");
    const result = document.getElementById("document-import-result");
    const open = document.getElementById("document-import-open");
    const submit = document.getElementById("document-import-submit");
    let busy = false;
    let timer;
    let delay = 2000;
    let currentTask;
    const keyName = "otterwiki-import:" + new URL(form.action).pathname;
    let requestKey;
    try { requestKey = sessionStorage.getItem(keyName); } catch (_) { /* 隐私模式可能禁用存储 */ }

    function key() {
        if (!requestKey) {
            const bytes = new Uint8Array(16);
            window.crypto.getRandomValues(bytes);
            requestKey = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
            try { sessionStorage.setItem(keyName, requestKey); } catch (_) { /* 仍可在当前页去重 */ }
        }
        return requestKey;
    }
    function clearKey() {
        requestKey = null;
        try { sessionStorage.removeItem(keyName); } catch (_) { /* 无需持久化 */ }
    }
    function setBusy(value) {
        busy = value;
        submit.disabled = value;
        form.setAttribute("aria-busy", String(value));
    }
    function showError(text) {
        panel.hidden = false;
        message.textContent = text;
    }
    function schedule(url) {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => poll(url), delay);
    }
    function display(task) {
        currentTask = task;
        panel.hidden = false;
        phase.textContent = task.phase_label;
        message.textContent = task.error || "";
        elapsed.textContent = task.elapsed_seconds;
        counts.textContent = task.total != null ? `${task.completed} / ${task.total} 个文件` : "";
        if (task.extracted_bytes) counts.textContent += `，已解压 ${(task.extracted_bytes / 1048576).toFixed(1)} MiB`;
        bar.removeAttribute("value");
        if (task.total > 0) bar.value = Math.min(100, task.completed / task.total * 100);
        if (task.status === "queued" || task.status === "running") {
            setBusy(true);
            schedule(task.status_url);
            return;
        }
        window.clearTimeout(timer);
        setBusy(Boolean(task.maintenance));
        clearKey();
        if (task.status === "succeeded") {
            phase.textContent = "文档仓库重建完成";
            bar.value = 100;
            const data = task.result || {};
            result.textContent = [
                `Markdown 页面：${data.pages}，附件：${data.assets}`,
                `已重写链接：${data.rewritten_links}，未解析引用：${data.unresolved_links}`,
                `Git 提交：${data.commit}`, data.verification || "",
                ...(data.warnings || [])
            ].join("\n");
            open.href = task.document_url;
            open.hidden = false;
        } else {
            phase.textContent = task.status === "interrupted" ? "导入已中断，请核对结果" : "导入失败";
            open.hidden = true;
        }
    }
    async function poll(url) {
        try {
            const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
            const isJSON = (response.headers.get("Content-Type") || "").includes("application/json");
            if (response.status === 401 || response.status === 403 || (response.redirected && !isJSON)) {
                showError("登录已失效或权限已撤销，请重新登录后查看任务。任务不会因此取消。");
                return;
            }
            if (response.status === 404) {
                showError("任务不存在或不属于当前空间，请返回导入页面核对。");
                return;
            }
            if (!response.ok || !isJSON) throw new Error("暂时无法查询");
            const task = await response.json();
            delay = 2000;
            display(task);
        } catch (_) {
            showError("暂时无法获取进度，正在重试；这不代表导入失败。");
            delay = Math.min(10000, delay * 2);
            schedule(url);
        }
    }
    form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (busy || !window.confirm("确定重建当前空间全部文档和 Git 历史吗？导入期间该空间将暂停访问。")) return;
        window.clearTimeout(timer);
        currentTask = null;
        setBusy(true);
        panel.hidden = false;
        result.textContent = counts.textContent = message.textContent = "";
        open.hidden = true;
        phase.textContent = "正在上传导入源";
        bar.value = 0;
        elapsed.textContent = "0";
        const data = new FormData(form);
        data.set("request_key", key());
        const xhr = new XMLHttpRequest();
        xhr.open("POST", form.action);
        xhr.setRequestHeader("Accept", "application/json");
        xhr.upload.onprogress = function (event) {
            if (event.lengthComputable) {
                bar.value = event.loaded / event.total * 100;
                counts.textContent = `已上传 ${(event.loaded / 1048576).toFixed(1)} / ${(event.total / 1048576).toFixed(1)} MiB`;
                if (event.loaded === event.total) phase.textContent = "上传完成，等待服务器接收确认";
            }
        };
        function uncertain() {
            setBusy(false);
            showError("未能确认提交结果，请刷新页面查看已有任务，勿直接重复导入。");
        }
        xhr.onerror = uncertain;
        xhr.ontimeout = uncertain;
        xhr.onload = function () {
            let body;
            try { body = JSON.parse(xhr.responseText); } catch (_) { uncertain(); return; }
            if (xhr.status === 202 && body.id) {
                delay = 2000;
                display(body);
            } else {
                setBusy(false);
                showError(body.error || "提交未成功，请重新登录并检查导入源。");
            }
        };
        xhr.send(data);
    });
    const initial = JSON.parse(document.getElementById("document-import-task").textContent);
    if (initial) display(initial);
    window.addEventListener("pagehide", () => window.clearTimeout(timer));
    window.addEventListener("pageshow", event => {
        if (event.persisted && currentTask && ["queued", "running"].includes(currentTask.status)) poll(currentTask.status_url);
    });
}());
