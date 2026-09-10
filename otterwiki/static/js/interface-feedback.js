/*
 * Shared interface-feedback toast helper.
 *
 * The interface management pages render feedback prompts as fixed,
 * full-width, in-flow alert bars (`.system-feedback` / `.dashboard-error`),
 * which is intrusive. This script turns them into floating toasts pinned to
 * the top-right corner: they slide in, auto-dismiss after a short delay, and
 * offer a close button. It reacts to the existing `hidden` attribute toggling
 * performed by each page's `showFeedback`/`showError` code, so none of those
 * files need changing.
 */
(function () {
    "use strict";

    var AUTO_HIDE_MS = 4000;
    var FADE_MS = 350;

    function manage(element) {
        if (element.dataset.toastManaged === "true") {
            return;
        }
        element.dataset.toastManaged = "true";

        var hideTimer = null;
        var fadeTimer = null;

        function dismiss() {
            clearTimeout(hideTimer);
            hideTimer = null;
            element.classList.add("is-hiding");
            fadeTimer = setTimeout(function () {
                element.hidden = true;
                element.classList.remove("is-hiding");
                fadeTimer = null;
            }, FADE_MS);
        }

        function onShown() {
            // Setting `textContent` (done by the page before unhiding) wipes
            // any previous children, so (re)create the dismiss button here.
            var button = element.querySelector(".close");
            if (!button) {
                button = document.createElement("button");
                button.type = "button";
                button.className = "close";
                button.setAttribute("aria-label", "关闭");
                button.innerHTML = "&times;";
                element.appendChild(button);
            }
            button.onclick = dismiss;

            clearTimeout(hideTimer);
            clearTimeout(fadeTimer);
            hideTimer = null;
            fadeTimer = null;
            element.classList.remove("is-hiding");
            hideTimer = setTimeout(dismiss, AUTO_HIDE_MS);
        }

        var observer = new MutationObserver(function () {
            if (element.hidden) {
                clearTimeout(hideTimer);
                clearTimeout(fadeTimer);
                hideTimer = null;
                fadeTimer = null;
                return;
            }
            // 提示已经显示时再次提示只会改写文本，`hidden` 属性不变；
            // 监听字符变化才能重建关闭按钮并重置自动隐藏计时器。
            onShown();
        });
        observer.observe(element, {
            attributes: true,
            attributeFilter: ["hidden"],
            characterData: true,
            childList: true,
            subtree: true
        });

        if (!element.hidden) {
            onShown();
        }
    }

    function initialize() {
        Array.prototype.forEach.call(
            document.querySelectorAll(".system-feedback, .dashboard-error"),
            manage
        );
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize);
    } else {
        initialize();
    }
}());
