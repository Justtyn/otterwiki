# 定制功能回归检查报告（2026-09）

检查范围：本分支相对上游 `redimp/otterwiki` 的 46 个定制提交（约 3.2 万行新增），
覆盖接口管理、结构化导航、文档导入/同步、多空间与权限、整站备份、UI/UX 与工程化。

检查方式：全量 pytest 基线、AST 死代码扫描、未使用 import 扫描、
CSS 类引用比对、模板/静态资源引用比对，以及针对关键缺陷的**可复现验证**
（脚本均使用临时仓库，未触碰 `app-data/`）。

状态列含义：`已修复` / `已记录`（本次不改，作为已知限制）/ `待办`。

---

## 0. 基线

```
OTTERWIKI_SETTINGS="" venv/bin/pytest tests -q
→ 945 passed, 4 failed（全部为 headless Chromium 无法在受限环境启动，见 D1）
```

修复后复跑全量：
```
OTTERWIKI_SETTINGS="" venv/bin/pytest -q
→ 968 passed（含浏览器测试，受限环境加 --no-sandbox 后通过）
```

死代码扫描（AST + 全仓引用计数，覆盖 `.py/.html/.js/.css/.md`）：
定制模块 349 个定义中仅 8 个零引用，其中 7 个是框架注册点
（`@app.context_processor`、`@migration`、`@app.cli.command`），
1 个为 CLI 入口。未使用 import 全仓 37 处（定制模块引入 1 处）。

结论：**没有大面积的死代码**。问题集中在边界条件、并发与安全校验上。

---

## 1. A 级：导致 500 / 数据不一致 / 安全边界失守

### A1 `.sidebar.json` 自引用导致 RecursionError（整命名空间侧栏 500）

- 位置：`otterwiki/structured_navigation.py:831`、`:854`、`:865`、`:918`
- 复现：

  ```
  apstack6/components/c1/.sidebar.json = [{"title":"Self","path":"."}]
  StructuredNavigation.for_page('apstack6/components/c1/index').build()
  → RecursionError: maximum recursion depth exceeded
  ```

- 原因：`sanitize_pagename` 会删除所有 `.` 字符，`_clean_repo_path(".") == ""`，
  于是 `join_path([base, ""])` 回指自身目录；
  `_build_spec_entry → _entry_for_target → _build_directory →
  _build_configured_children` 整条链路没有环保护
  （只有 `_directory_landing_file` 有 `visited`）。
- 影响：任何写入者上传一份自引用规则，该命名空间**每个页面**的侧栏渲染 500，
  且无降级路径。
- 状态：**已修复**（环保护 + 回退到普通页面索引）。

### A2 Git 首次导入后默认空间推送被拒

- 位置：`otterwiki/repository_sync.py:280-310`；对照 `otterwiki/remote.py:12-29`
- 原因：`_import_repository` 用 `git.Repo.clone_from` 后直接替换仓库，
  未写 `receive.denyCurrentBranch=updateInstead`；`GitHttpServer` 只在进程启动时
  对 `REPOSITORY` 写一次该配置。APStack 导入路径
  （`document_import.py:291-293`）**有**这一步，两条路径行为不一致。
- 影响：默认空间执行一次 Git 首次导入后，`git push` 报
  `refusing to update checked out branch`，必须重启进程才恢复。
- 状态：**已修复**（抽取 `remote.ensure_push_config`，两条路径共用）。

### A3 两条「替换仓库」流程可并发替换同一空间

- 位置：`otterwiki/import_tasks.py:217-286` vs `otterwiki/repository_sync.py:566-621`
- 原因：APStack 导入持全局 `executor.lock`、互斥依据是查 DB；
  Git 同步持按空间 `space-<id>/executor.lock`、互斥依据是 `blocked()`。
  `submit_task` 查库后才写 journal，中间隔着可能 1GB 的 `save_upload`，
  窗口内 Git 首次导入可以启动。
- 影响：双重 `os.replace`、输者 `storage.repo` 指向已改名目录、
  任务结果记录与实际仓库不一致，且双方各自删除自己的备份记录，
  原仓库可能在「两次都成功」的假象下被删。
- 状态：**已修复**（登记先于上传：维护日志（含 `target`）在 `save_upload`
  之前写入，Git 同步侧的 `blocked()` 在整个上传期间都能看到本任务；
  登记后复查活动 Git 任务，冲突时撤销登记并返回 409，不再有可观测窗口）。

### A4 远程仓库地址校验收紧

- 位置：`otterwiki/repository_sync.py:74-92`
- 复现（实测放行）：

  ```
  EXT::touch /tmp/x         # 大小写绕过 ext:: 黑名单
  http://127.0.0.1:8080/r.git
  http://169.254.169.254/x  # 云元数据端点
  ssh://root@localhost/r.git
  https://[::1]/r.git
  C:\repo
  ```

- 说明：`EXT::` 在本机实测不构成 RCE（git 报 `remote-EXT` 不存在），
  但协议黑名单形同虚设；内网/回环 SSRF 成立。
- 状态：**已修复**（casefold 黑名单 + scheme 白名单 + 私网/回环拒绝）。

### A5 切换现场分类不完备 → 永久维护态

- 位置：`otterwiki/document_import.py:426-436`、`otterwiki/import_runtime.py:240-281`
- 原因：`checkpoint()` 先写 `maintenance=True`，之后才判断 `not target.is_dir()`；
  target 被外部删除时四条恢复分支全不成立 → 落到 `else`，维护态永不解除，
  且应用内没有解除入口。损坏 journal 的 `target="*"` 还会阻塞整站。
- 状态：**已修复**（写 journal 前校验 target；新增「未开始切换」恢复分支）。

### A6 扫描任务上传 256MiB 全量读入内存

- 位置：`otterwiki/views.py:883-937`、`otterwiki/interface_management.py:202-250`
- 原因：先读入内存再 `b"".join` 复制一份（峰值约 2×），全仓未设置
  `MAX_CONTENT_LENGTH`，4 请求线程下可 OOM。
- 状态：**已修复**（流式构造 multipart + 上传上限配置化）。

---

## 2. B 级：功能缺陷

| 编号 | 位置 | 问题 | 状态 |
|---|---|---|---|
| B1 | `structured_navigation.py:113-118` | 复用 `sanitize_pagename` 会删除所有点号：`v1.2/guide → v12/guide`（实测），版本号目录 404 或被静默丢弃 | 已修复 |
| B2 | `structured_navigation.py:1008-1015` | 缓存键用 git revision，内容却读工作树 → 未提交的新规则/页面在下次 commit 前导航不可见 | 已修复 |
| B3 | `structured_navigation.py:690-694`、`wiki.py:1588-1602` | `index.html` 作为导航落地页，经 `send_file` 以 `text/html` 内联返回仓库原始 HTML → 同源存储型 XSS | 已修复 |
| B4 | `navigation_editor.py:233-249`、`gitstorage.py:508-534` | `.navigation.json` 整份读-改-写、非原子写、无版本校验 → 并发保存静默丢失，中断留下截断 JSON | 已修复 |
| B5 | `structured_navigation.py:1066-1123` | 若 `###` 先于 `##` 出现，首个标题编号为 "0.1"；跨级跳跃产生 "1.1.0.1" | 已修复 |
| B6 | `structured_navigation.py:349-357` | `section` 按硬编码路径判定，而编辑器允许改标签路径 → 自定义标签高亮错位 | 已修复 |
| B7 | `structured_navigation.py:658-662`、`gitstorage.py:703-737` | `storage.list(depth=0)` 不剪枝 `os.walk` → 树构建成本 = 所有目录子树大小之和 | 已修复 |
| B8 | `import_tasks.py:367-390` | `Progress.__call__` 未判 None、未捕获 DB 异常 → 一次抖动即把导入判为失败 | 已修复 |
| B9 | `import_tasks.py:241-251` | `OperationalError` 静默降级为「无冲突」，与 `repository_sync.py:673-687` 的显式暴露相反 | 已修复 |
| B10 | `import_runtime.py:186-196` | 超时文案硬编码 60 秒，`site_backup_web.py:153` 实际传 30 | 已修复 |
| B11 | `document_import.py:401-414,476-478` | `_cleanup_repository_backups` 生产不可达 → `.pre-import-*` 备份堆积 | 已修复 |
| B12 | `interface_management.py:171-199` | 未捕获 `http.client.HTTPException`、深嵌套 `RecursionError`、畸形 IPv6 `ValueError` → 500 | 已修复 |
| B13 | `views.py` 变更类端点 | 原样回传上游响应，绕过白名单归一化，无 `Cache-Control: no-store` | 已修复 |
| B14 | `interface_management.py:352-604` 等 | `quote(safe='')` 不编码 `.`，`..` 路径段透传上游 | 已修复 |
| B15 | `interface_management.py:82-88,265-283,1477-1492` | `_text(0)==""`、`_count("17")==0`、`bool("false") is True`；`packageSourceType` 未校验未转发 | 已修复 |
| B16 | `repository_sync.py:584-621` | `thread.start()` 抛错时锁永不释放（APStack 侧已正确处理） | 已修复 |
| B17 | `repository_sync.py:451,466-517,878-884` | 只捕获 `Exception`；`get_admin_task` 不调 `recover_tasks()`；前端无轮询上限 → 永久 running | 已修复 |
| B18 | `static/js/interface-feedback.js:60-74` | 提示已显示时二次提示不重建关闭按钮、不重置计时器 | 已修复 |
| B19 | `templates/interface_management.html:629` | 「关联服务」列渲染 `relAttrsJson` 原始 JSON | 已修复 |
| B20 | `defaults.py` | `APSTACK_SCAN_UPLOAD_MAX_SIZE` 未登记 → 不进整站备份的 `SITE_KEYS` | 已修复 |

---

## 3. C 级：无用代码与重复实现

已清理：

- `static/js/simple-datatables@10.2.0.js`（非 min 版，全仓零引用）
- `interface_management.py` 的 `SCAN_TASK_STATUSES`、`dashboard_summary_url()`
- `templates/interface_management.html:860-866` 不可达占位分支、`data-active-tab`
- `static/css/interface-management.css` 的 `.management-placeholder`、
  `static/css/settings.css` 的 `.settings-kv`
- `import_tasks.py` 未使用的 `blocked` 导入
- `structured_navigation.py:516-520` 恒等三元表达式、`NavigationTree.title` 重复默认值
- `document_import.py` 的 `repo_commit_for_import` / `latest_import_task` 纯转发
- `document_import.py:435` `target.is_symlink()`（`.resolve()` 之后恒为假）

已收敛：`_as_bool` 三份 → `util.py` 单实现；分页归一化与 clamp 抽公共函数。

已记录（本次不改）：

- `templates/example.html`：上游遗留的示例模板，零引用但非本分支引入，保留。
- 路径/符号链接校验四处重复（`document_import.py:158-173`、
  `repository_sync.py:210-217`、`site_backup.py:334-380`、`:204-229`）：
  规则各不相同，合并属跨模块重构，留待专项处理。
- `render_context()`：生产零调用（仅测试使用）；当前没有请求外的批量渲染入口，
  保留并补充注释说明其适用场景。

---

## 4. D 级：测试与工程化

| 编号 | 问题 | 状态 |
|---|---|---|
| D1 | 浏览器测试直接以 `--headless` 启动 Chromium，缺 `--no-sandbox`，受限环境/CI 直接失败；无浏览器时 `skip` 静默零覆盖 | 已修复（统一 `--no-sandbox` 参数；`OTTERWIKI_REQUIRE_BROWSER_TESTS=1` 时无浏览器直接失败，避免静默零覆盖） |
| D2 | `test_space_repository_sync.py` 把 `recover_tasks` 打桩，Git 同步崩溃恢复零覆盖 | 已补 |
| D3 | 未覆盖「phase=switching 但仓库未变」这一最常见崩溃窗口 | 已补 |
| D4 | `test_space_repository_sync.py:44-62` 用 `try/except ValueError` 接受任意错误、不校验文案；另有用例直接构造记录绕过 `validate_remote_url` | 已修复 |
| D5 | ZIP/上传安全边界仅覆盖 traversal + symlink；`save_upload`、`_validate_selected_files` 零覆盖 | 已补 |
| D6 | 导航编辑器校验分支零覆盖；结构化导航无环规则 fixture；缓存断言依赖累计 `misses` | 已补 |
| D7 | 接口管理变更类端点无 403 覆盖；外部请求错误路径零覆盖 | 已补 |

---

## 5. E 级：文档与配置一致性

- **E1 匿名访问**：`settings.cfg.skeleton:32`、`help_admin.md:82` 仍写
  `READ_ACCESS = 'ANONYMOUS'` 表示「任何人无需登录即可访问」，但多空间门禁要求
  所有非豁免端点登录（实测 `/`、`/Home`、`/sitemap.xml`、`/-/index` 均 302 到登录）。
  该行为**已固化在 `tests/test_spaces.py:75`、`tests/test_auth.py:219-226`**，
  是有意设计而非缺陷；本次只修正文档与模板说明。
- **E2** `CHANGELOG-DEV.md` 补「回归检查结论」与已知限制。
- **E3** `docs/multi-space-upgrade.md` 补充：匿名访问被门禁覆盖、
  `PROXY_HEADER` 与多空间不兼容的具体表现（非管理员静默 404）。

---

## 6. 已知限制（本次不修复，需在部署时注意）

1. **单副本、单 Web 进程假设**：`_readers`、`_process_locks`、`_site_maintenance`、
   `SiteGate` 均为进程内状态，仅在 `uwsgi` 下校验 `numproc`。
   gunicorn 多 worker 或多副本部署下，维护门禁与「请求排空」会**静默失效**，
   仓库可能在其它 worker 正在读取时被替换。部署务必保持
   `docker/uwsgi.ini` 的单进程 4 线程形态。
2. **`PROXY_HEADER` 与多空间不兼容**：代理头登录的用户没有数据库账号，
   不参与空间授权，非管理员会静默 404；启动时仅打印告警。
3. **匿名访问被门禁覆盖**：见 E1，与 `READ_ACCESS=ANONYMOUS` 的字面含义不符。
4. **凭据管理**：仓库密文经环境变量传给 git 子进程；`SECRET_KEY` 轮换后
   既有密文不可解，需在管理页重新录入。
5. **`ATTACHMENT_ACCESS` 与结构化导航**：结构化导航会把目录内 `index.html`
   链接进主导航（现已强制下载，见 B3）。

---

## 7. 复现与验证命令

```bash
# 基线（需在仓库根目录执行）
OTTERWIKI_SETTINGS="" venv/bin/pytest tests -q

# A1：结构化导航自引用
#   构造 apstack6/.portal.yml + .idp/.model.yaml + 组件目录，
#   并在该目录写入 .sidebar.json = [{"title":"Self","path":"."}]
OTTERWIKI_SETTINGS="" venv/bin/pytest tests/test_structured_navigation.py -q -k "self_reference or cycle"

# A4：远程地址校验
OTTERWIKI_SETTINGS="" venv/bin/pytest tests/test_space_repository_sync.py -q -k "validation"

# B1：点号路径
OTTERWIKI_SETTINGS="" venv/bin/pytest tests/test_structured_navigation.py -q -k "dotted"

# D1：浏览器测试（受限环境）
OTTERWIKI_SETTINGS="" venv/bin/pytest tests/test_import_browser.py tests/test_document_font_size.py -q
```
