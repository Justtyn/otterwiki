(function () {
    "use strict";

    var MIN_COLUMN_WIDTH = 80;
    var KEYBOARD_STEP = 16;

    function columnLabel(header, index) {
        return (header.textContent || "").trim() || "第 " + (index + 1) + " 列";
    }

    function setColumnWidth(table, column, width, tableWidth) {
        var nextWidth = Math.max(MIN_COLUMN_WIDTH, Math.round(width));
        column.style.width = nextWidth + "px";
        table.style.width = Math.max(nextWidth, Math.round(tableWidth)) + "px";
    }

    function currentColumnWidth(column, header) {
        var configuredWidth = parseFloat(column.style.width);
        return configuredWidth || header.getBoundingClientRect().width;
    }

    function addResizeHandle(table, column, header, index) {
        var handle = document.createElement("span");
        var label = columnLabel(header, index);

        handle.className = "table-column-resizer";
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-orientation", "vertical");
        handle.setAttribute("aria-label", "调整“" + label + "”列宽");
        handle.setAttribute("tabindex", "0");
        header.appendChild(handle);

        handle.addEventListener("pointerdown", function (event) {
            if (event.button !== undefined && event.button !== 0) {
                return;
            }

            var startX = event.clientX;
            var startWidth = currentColumnWidth(column, header);
            var startTableWidth = table.getBoundingClientRect().width;

            event.preventDefault();
            handle.classList.add("is-dragging");
            document.documentElement.classList.add("is-resizing-table-column");
            if (handle.setPointerCapture && event.pointerId !== undefined) {
                handle.setPointerCapture(event.pointerId);
            }

            function move(moveEvent) {
                var nextWidth = Math.max(
                    MIN_COLUMN_WIDTH,
                    startWidth + moveEvent.clientX - startX
                );
                var delta = nextWidth - startWidth;
                setColumnWidth(table, column, nextWidth, startTableWidth + delta);
            }

            function stop() {
                handle.classList.remove("is-dragging");
                document.documentElement.classList.remove("is-resizing-table-column");
                document.removeEventListener("pointermove", move);
                document.removeEventListener("pointerup", stop);
                document.removeEventListener("pointercancel", stop);
            }

            document.addEventListener("pointermove", move);
            document.addEventListener("pointerup", stop);
            document.addEventListener("pointercancel", stop);
        });

        handle.addEventListener("keydown", function (event) {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
                return;
            }

            event.preventDefault();
            var direction = event.key === "ArrowRight" ? 1 : -1;
            var step = event.shiftKey ? KEYBOARD_STEP * 2.5 : KEYBOARD_STEP;
            var startWidth = currentColumnWidth(column, header);
            var nextWidth = Math.max(MIN_COLUMN_WIDTH, startWidth + direction * step);
            var tableWidth = table.getBoundingClientRect().width + nextWidth - startWidth;
            setColumnWidth(table, column, nextWidth, tableWidth);
        });
    }

    function makeResizable(table) {
        if (table.dataset.columnsResizable === "true") {
            return;
        }

        var headers = Array.prototype.slice.call(
            table.querySelectorAll("thead tr:first-child > th")
        );
        if (!headers.length) {
            return;
        }

        var tableWidth = table.getBoundingClientRect().width;
        var colgroup = document.createElement("colgroup");
        var columns = headers.map(function (header) {
            var column = document.createElement("col");
            column.style.width = header.getBoundingClientRect().width + "px";
            colgroup.appendChild(column);
            return column;
        });

        table.insertBefore(colgroup, table.firstChild);
        table.style.width = tableWidth + "px";
        table.dataset.columnsResizable = "true";
        headers.forEach(function (header, index) {
            addResizeHandle(table, columns[index], header, index);
        });
    }

    function initialize() {
        Array.prototype.forEach.call(
            document.querySelectorAll(".interface-main table.system-table"),
            makeResizable
        );
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize);
    } else {
        initialize();
    }
})();
