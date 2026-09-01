(function () {
    "use strict";

    var MIN_COLUMN_WIDTH = 96;
    var KEYBOARD_STEP = 16;
    var STORAGE_PREFIX = "interfaceTableWidths:";

    function rounded(value) {
        return Math.max(0, Math.round(value));
    }

    function columnLabel(header, index) {
        return (header.textContent || "").trim() || "第 " + (index + 1) + " 列";
    }

    function findActionIndex(headers) {
        for (var i = headers.length - 1; i >= 0; i--) {
            if ((headers[i].textContent || "").trim() === "操作") {
                return i;
            }
        }
        return -1;
    }

    // Measure the intrinsic width of a piece of text using an offscreen element
    // so we are not limited by the column's current width (which may be clipped).
    function naturalTextWidth(text, style) {
        if (!text || typeof document === "undefined") {
            return 0;
        }
        var span = document.createElement("span");
        span.style.position = "absolute";
        span.style.visibility = "hidden";
        span.style.whiteSpace = "nowrap";
        span.style.fontSize = style.fontSize;
        span.style.fontWeight = style.fontWeight;
        span.style.fontFamily = style.fontFamily;
        if (style.letterSpacing) {
            span.style.letterSpacing = style.letterSpacing;
        }
        span.textContent = text;
        document.body.appendChild(span);
        var width = span.getBoundingClientRect().width;
        document.body.removeChild(span);
        return width;
    }

    function horizontalPadding(element) {
        if (!element) {
            return 0;
        }
        var style = window.getComputedStyle(element);
        return (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    }

    function computedMinWidth(element) {
        if (!element) {
            return 0;
        }
        var value = parseFloat(window.getComputedStyle(element).minWidth);
        return isNaN(value) ? 0 : value;
    }

    // Natural width of a table header cell (its label + horizontal padding).
    function headerNaturalWidth(header) {
        var style = window.getComputedStyle(header);
        return naturalTextWidth((header.textContent || "").trim(), style) + horizontalPadding(header);
    }

    function setWidths(columns, widths) {
        columns.forEach(function (column, index) {
            column.style.width = Math.round(widths[index]) + "px";
        });
    }

    function sumWidths(config) {
        var total = 0;
        config.widths.forEach(function (width) { total += width; });
        return total;
    }

    // ---------------- persistence ----------------

    function storageKey(table) {
        var cls = (table.className || "").trim();
        return STORAGE_PREFIX + cls.replace(/\s+/g, "-");
    }

    function loadStoredWidths(config) {
        try {
            var raw = window.localStorage.getItem(storageKey(config.table));
            if (!raw) {
                return null;
            }
            var parsed = JSON.parse(raw);
            if (Array.isArray(parsed) && parsed.length === config.widths.length) {
                return parsed;
            }
        } catch (error) {
            // localStorage can throw (private mode, disabled, etc.)
        }
        return null;
    }

    function persist(config) {
        try {
            window.localStorage.setItem(storageKey(config.table), JSON.stringify(config.widths));
        } catch (error) {
            // ignore – persistence is best-effort
        }
    }

    // ---------------- width distribution ----------------

    // Columns ordered by physical proximity to `index` (closest neighbour first,
    // preferring the column to the right), skipping the fixed action column.
    function neighborSequence(config, index) {
        var n = config.widths.length;
        var sequence = [];
        for (var d = 1; d < n; d++) {
            var right = index + d;
            if (right < n) {
                sequence.push(right);
            }
            var left = index - d;
            if (left >= 0) {
                sequence.push(left);
            }
        }
        return sequence.filter(function (j) {
            return j !== index && config.resizable.indexOf(j) !== -1;
        });
    }

    function columnMin(config, index) {
        return config.minWidths[index] || MIN_COLUMN_WIDTH;
    }

    // Move `amount` of width into/out of the neighbouring columns so the total
    // stays constant. A positive amount grows the nearest column; a negative
    // amount shrinks it (clamped to its own minimum), cascading only when needed.
    function absorbIntoNeighbors(config, index, amount) {
        var widths = config.widths;
        var sequence = neighborSequence(config, index);
        var remaining = amount;
        for (var k = 0; k < sequence.length && remaining !== 0; k++) {
            var j = sequence[k];
            if (remaining > 0) {
                widths[j] += remaining;
                remaining = 0;
            } else {
                var take = Math.min(-remaining, widths[j] - columnMin(config, j));
                widths[j] -= Math.max(0, take);
                remaining += take;
            }
        }
        return remaining;
    }

    // The table must never show a gap on the right, so the sum of the column
    // widths stays at or above the container width. If a drag pushes it below,
    // only the nearest column grows to absorb the freed space.
    function fillToTarget(config, index) {
        var total = sumWidths(config);
        if (total < config.targetWidth) {
            absorbIntoNeighbors(config, index, config.targetWidth - total);
        }
    }

    // Table box = max(container, sum of columns), so it fills by default and
    // overflows (horizontal scroll) once the columns are wider than the container.
    function syncTableWidth(config) {
        var width = Math.max(config.targetWidth, Math.round(sumWidths(config)));
        config.table.style.width = width + "px";
    }

    // Resize a single column independently. Widening may push the total past the
    // container (the table then scrolls horizontally) and never moves the other
    // columns; narrowing is only clamped by the fill floor so the table never
    // leaves a gap. The caller is responsible for persisting the result.
    function resizeColumn(config, index, nextWidth) {
        config.widths[index] = Math.max(columnMin(config, index), Math.round(nextWidth));
        fillToTarget(config, index);
        syncTableWidth(config);
        setWidths(config.columns, config.widths);
    }

    // The action column must be exactly as wide as its buttons/links so it can
    // never be stretched or collapsed. Re-measure whenever rows are re-rendered.
    // Because its width is content-driven, a change is absorbed by the nearest
    // column so it never introduces an unintended horizontal scrollbar.
    function updateActionColumn(config) {
        if (config.actionIndex < 0) {
            return;
        }
        var header = config.headers[config.actionIndex];
        var maxWidth = Math.max(
            rounded(headerNaturalWidth(header)),
            rounded(computedMinWidth(header))
        );
        Array.prototype.forEach.call(config.table.querySelectorAll("tbody tr"), function (row) {
            var cell = row.children[config.actionIndex];
            if (!cell) {
                return;
            }
            var contentWidth = 0;
            Array.prototype.forEach.call(cell.children, function (child) {
                contentWidth += (child.scrollWidth || child.offsetWidth || 0);
            });
            if (contentWidth > 0) {
                contentWidth += horizontalPadding(cell);
                if (contentWidth > maxWidth) {
                    maxWidth = contentWidth;
                }
            }
        });
        var nextWidth = rounded(maxWidth);
        var currentWidth = config.widths[config.actionIndex];
        if (nextWidth === currentWidth) {
            return;
        }
        config.widths[config.actionIndex] = nextWidth;
        absorbIntoNeighbors(config, config.actionIndex, currentWidth - nextWidth);
        fillToTarget(config, config.actionIndex);
        syncTableWidth(config);
        setWidths(config.columns, config.widths);
        persist(config);
    }

    function addResizeHandle(config, column, header, index) {
        var handle = document.createElement("span");
        var label = columnLabel(header, index);

        handle.className = "table-column-resizer";
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-orientation", "vertical");
        handle.setAttribute("aria-label", "调整“" + label + "”列宽");
        handle.setAttribute("tabindex", "0");
        header.appendChild(handle);

        function currentWidth() {
            var configured = parseFloat(column.style.width);
            return configured || config.widths[index];
        }

        handle.addEventListener("pointerdown", function (event) {
            if (event.button !== undefined && event.button !== 0) {
                return;
            }
            var startX = event.clientX;
            var startWidth = currentWidth();

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
                resizeColumn(config, index, nextWidth);
            }

            function stop() {
                handle.classList.remove("is-dragging");
                document.documentElement.classList.remove("is-resizing-table-column");
                document.removeEventListener("pointermove", move);
                document.removeEventListener("pointerup", stop);
                document.removeEventListener("pointercancel", stop);
                persist(config);
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
            resizeColumn(config, index, currentWidth() + direction * step);
            persist(config);
        });
    }

    // The minimum usable width is the container, but never below the table's own
    // CSS min-width (e.g. 82rem / 190rem), which is what prevents a gap.
    function computeTargetWidth(table) {
        var containerWidth = table.parentElement
            ? table.parentElement.clientWidth
            : table.getBoundingClientRect().width;
        var minTableWidth = computedMinWidth(table);
        return Math.max(containerWidth, minTableWidth);
    }

    function buildConfig(table) {
        var headerRow = table.querySelector("thead tr");
        if (!headerRow) {
            return null;
        }
        var headers = Array.prototype.slice.call(headerRow.children);
        if (!headers.length) {
            return null;
        }

        var targetWidth = Math.round(computeTargetWidth(table));
        if (!targetWidth) {
            return null;
        }

        var colgroup = document.createElement("colgroup");
        var columns = headers.map(function () {
            var column = document.createElement("col");
            colgroup.appendChild(column);
            return column;
        });
        table.insertBefore(colgroup, table.firstChild);

        var actionIndex = findActionIndex(headers);
        var resizable = [];
        var widths = [];
        headers.forEach(function (header, index) {
            if (index !== actionIndex) {
                resizable.push(index);
            }
            widths.push(0);
        });

        var config = {
            table: table,
            headers: headers,
            columns: columns,
            widths: widths,
            actionIndex: actionIndex,
            resizable: resizable,
            targetWidth: targetWidth,
            observer: null
        };

        // Every column has a minimum width so it can never be dragged below the
        // width needed by its (nowrap) header label.
        config.minWidths = headers.map(function (header, index) {
            if (index === actionIndex) {
                return Math.max(
                    rounded(headerNaturalWidth(header)),
                    rounded(computedMinWidth(header))
                );
            }
            return Math.max(MIN_COLUMN_WIDTH, rounded(headerNaturalWidth(header)));
        });

        // Restore previously saved widths (only for the flexible columns); the
        // action column is always re-measured from its content.
        var stored = loadStoredWidths(config);
        if (stored) {
            resizable.forEach(function (index) {
                config.widths[index] = Math.max(config.minWidths[index], rounded(stored[index]));
            });
        } else {
            var actionMin = actionIndex >= 0 ? config.minWidths[actionIndex] : 0;
            config.widths[actionIndex] = actionMin;
            var shareCount = resizable.length;
            var share = shareCount ? Math.floor((targetWidth - actionMin) / shareCount) : 0;
            resizable.forEach(function (index) {
                config.widths[index] = Math.max(config.minWidths[index], share);
            });
        }

        // Always reseed the action column width so it matches the current content.
        if (actionIndex >= 0) {
            config.widths[actionIndex] = config.minWidths[actionIndex];
        }

        fillToTarget(config, actionIndex >= 0 ? actionIndex : -1);
        syncTableWidth(config);
        setWidths(config.columns, config.widths);

        return config;
    }

    // Watch the tbody so the action column is re-measured (and the table kept
    // filled) whenever rows are loaded/re-rendered.
    function observeRows(config) {
        var body = config.table.tBodies[0];
        if (!body || typeof MutationObserver === "undefined") {
            return;
        }
        config.observer = new MutationObserver(function () {
            updateActionColumn(config);
        });
        config.observer.observe(body, {
            childList: true,
            subtree: true,
            characterData: true
        });
    }

    // After the window is resized/zoomed, the recorded column widths are left
    // untouched; only the table box is adjusted so it still fills the container.
    function refreshForResize(config) {
        config.targetWidth = Math.round(computeTargetWidth(config.table));
        syncTableWidth(config);
        setWidths(config.columns, config.widths);
    }

    var configs = [];
    var resizeBound = false;

    function makeResizable(table) {
        if (table.dataset.columnsResizable === "true") {
            return;
        }
        var config = buildConfig(table);
        if (!config) {
            return;
        }
        table.dataset.columnsResizable = "true";

        config.headers.forEach(function (header, index) {
            if (index !== config.actionIndex) {
                addResizeHandle(config, config.columns[index], header, index);
            }
        });

        observeRows(config);
        configs.push(config);

        if (!resizeBound) {
            resizeBound = true;
            window.addEventListener("resize", function () {
                configs.forEach(refreshForResize);
            });
        }
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
