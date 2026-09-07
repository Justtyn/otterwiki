(function () {
  "use strict";
  const token = document.querySelector('meta[name="csrf-token"]');
  const csrfHeaders = token ? {"X-CSRFToken": token.content} : {};

  function render(card, task) {
    const target = card.querySelector(".repository-task-result");
    if (!target) return;
    const text = [task.operation, task.phase_label || task.phase, task.status].filter(Boolean).join(" · ");
    target.textContent = task.error ? text + "：" + task.error : text;
    target.className = "repository-task-result mt-10" + (task.status === "failed" ? " text-danger" : "");
  }

  async function poll(card, url, delay) {
    try {
      const response = await fetch(url, {headers: {"Accept": "application/json"}});
      const task = await response.json();
      if (!response.ok) throw new Error(task.error || "无法查询任务状态。");
      render(card, task);
      if (task.status === "queued" || task.status === "running") {
        window.setTimeout(() => poll(card, url, 2000), 2000);
      } else if (task.status === "succeeded" && task.operation === "import") {
        window.setTimeout(() => window.location.reload(), 700);
      } else {
        card.querySelectorAll(".repository-task").forEach((button) => {
          button.disabled = card.dataset.state !== "ready" && button.dataset.operation !== "import";
        });
      }
    } catch (error) {
      const target = card.querySelector(".repository-task-result");
      if (target) target.textContent = error.message;
      window.setTimeout(() => poll(card, url, Math.min(delay * 2, 15000)), delay);
    }
  }

  document.querySelectorAll(".repository-card").forEach((card) => {
    if (card.dataset.statusUrl) poll(card, card.dataset.statusUrl, 2000);
    card.querySelectorAll(".repository-task").forEach((button) => {
      button.addEventListener("click", async () => {
        const operation = button.dataset.operation;
        const data = new FormData();
        data.set("operation", operation);
        // randomUUID() is unavailable when the site is opened over plain
        // HTTP.  The server generates a request key when it is omitted.
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
          data.set(
            "request_key",
            window.crypto.randomUUID().replace(/-/g, "")
          );
        }
        if (operation === "import") {
          const expected = "IMPORT " + button.dataset.spaceSlug;
          const confirmation = window.prompt("首次导入将替换该空间当前内容。请输入：" + expected);
          if (confirmation === null) return;
          data.set("confirmation", confirmation);
        }
        button.disabled = true;
        try {
          const response = await fetch(card.dataset.taskUrl, {method: "POST", headers: csrfHeaders, body: data});
          const task = await response.json();
          if (!response.ok) throw new Error(task.error || "无法启动 Git 任务。");
          render(card, task);
          poll(card, task.status_url, 2000);
        } catch (error) {
          const target = card.querySelector(".repository-task-result");
          if (target) target.textContent = error.message;
          button.disabled = false;
        }
      });
    });
    const webhook = card.querySelector(".repository-webhook");
    if (webhook) webhook.addEventListener("click", async () => {
      const response = await fetch(card.dataset.webhookUrl, {method: "POST", headers: csrfHeaders});
      const data = await response.json();
      const target = card.querySelector(".repository-webhook-result");
      if (target) target.textContent = response.ok ? data.webhook_url : data.error;
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    const auth = form.querySelector(".repository-auth-type");
    if (!auth) return;
    const toggleCredentials = () => {
      form.querySelectorAll(".repository-https-field").forEach((field) => {
        field.style.display = auth.value === "https" ? "block" : "none";
      });
      form.querySelectorAll(".repository-ssh-field").forEach((field) => {
        field.style.display = auth.value === "ssh" ? "block" : "none";
      });
    };
    auth.addEventListener("change", toggleCredentials);
    toggleCredentials();
  });
}());
