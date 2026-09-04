# agent.md — 项目级 AI 代理基础提示词

> 本文件是给在此仓库中工作的 AI 编码代理（Claude Code、Copilot、Cursor 等）阅读的系统基础提示词。
> 任何 AI 代理在此仓库中生成或修改代码之前，都必须先理解并遵守本文约定。

## 1. 项目身份

本仓库是 [An Otter Wiki](https://otterwiki.com)（上游 [redimp/otterwiki](https://github.com/redimp/otterwiki)）的**中文化定制分支**（origin: Justtyn/otterwiki）。

- **性质**：基于 Python/Flask 的 Wiki 系统，所有内容以 Markdown 形式存储在 git 仓库中
- **许可证**：MIT（保留上游版权声明，不要删除）
- **Python 要求**：>= 3.11
- **定制目标**：汉化界面 + 对接 APStack6 产品文档生态（文档迁移、规则驱动导航、接口管理工作台）
- **上游关系**：remote `upstream` 指向 redimp/otterwiki。本分支基于上游 2.23.0 定制，上游 main 已到 2.24.x。**与上游同步（merge/rebase）时，必须保留本分支的汉化文案与 APStack 定制功能，不得回退。**

## 2. 必须遵守的约定（铁律）

1. **用户可见文案一律使用简体中文**。新增 UI 文案、flash 消息、表单提示、报错信息均写中文；代码注释、docstring、git commit message 也使用中文（沿用仓库现有风格）。
2. **不引入 AI/LLM 相关依赖**。本项目的界面与文档全部由人工维护，除非用户明确要求，不要添加任何大模型、提示词、聊天机器人相关功能。
3. **测试必须通过**。任何功能改动必须附带或更新 `tests/` 下的 pytest 用例，提交前运行 `make test`（或 `OTTERWIKI_SETTINGS="" venv/bin/pytest tests docs/plugin_examples/tests`）。
4. **代码风格**：black，`line-length = 79`，`skip-string-normalization = true`（保留单引号，不强制双引号）。所有 Python 文件头部保留 vim modeline 约定：`# vim: set et ts=8 sts=4 sw=4 ai:`。
5. **不改动运行时数据**：`app-data/`（wiki 内容仓库 + sqlite 数据库）与 `settings.cfg` 是本地运行时数据（已 gitignore），不要修改或提交；涉及配置示例时改 `settings.cfg.skeleton`。
6. **版本号由 tbump 管理**，只修改 `otterwiki/version.py` 的 `version_info`；CHANGELOG 由 git-changelog 基于 conventional commit 自动生成，不手写。

## 3. 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | Flask 3.1（`otterwiki.server` 为应用入口，Flask app 是模块级单例） |
| 存储 | git（`gitpython`），`otterwiki/gitstorage.py` 封装所有读写 |
| 渲染 | mistune 3（`otterwiki/renderer.py` + `renderer_plugins.py` + `renderer_embeddings.py`） |
| 数据库 | Flask-SQLAlchemy 2.0（仅存用户、偏好、草稿、缓存等元数据，不存页面内容） |
| 插件 | pluggy（`otterwiki/plugins.py`，示例见 `docs/plugin_examples/`） |
| 前端 | halfmoon CSS + CodeMirror 6（源码在 `frontend/src/`，esbuild 打包） |
| 认证/表单 | Flask-Login + flask-wtf（CSRF 全局开启） |

## 4. 仓库结构（关键文件职责）

```
otterwiki/
├── server.py            # Flask app 工厂：单例 app/db/storage，读取 settings.cfg，注册插件
├── wiki.py              # 核心页面逻辑（约 73KB：查看/编辑/保存/历史/附件/RSS 等）
├── views.py             # 所有 HTTP 路由（含本分支新增的 /-/interface/**、/-/admin/* 路由）
├── gitstorage.py        # git 存储层：commit/log/revert/rename/附件
├── auth.py / models.py / preferences.py / sidebar.py / pageindex.py
├── helper.py / util.py  # 通用工具（toast、sanitize_pagename、缓存等）
├── renderer.py          # mistune 渲染管线（含 HTML allowlist 消毒，安全敏感）
├── plugins.py           # pluggy 钩子定义与内置插件
├── # ---- 以下为本分支定制功能（上游没有，改动时重点保护）----
├── interface_management.py   # 接口管理工作台：代理调用外部 IDP API
├── structured_navigation.py  # 规则驱动的产品文档导航（.portal.yml/.model.yaml/.sidebar.json）
├── navigation_editor.py      # 管理员可视化导航编辑器
├── document_import.py        # 管理员触发的 APStack 文档导入/重置（调用 scripts 迁移器）
├── spaces.py                 # 多空间：WSGI 前缀中间件、访问门禁、空间仓库定位、上下文注入
├── migrations.py             # 版本化数据库迁移（flask db upgrade，可重复执行）
├── templates/admin/{spaces,space_edit,groups,group_edit}.html  # 空间/组管理界面
├── templates/interface_management.html
├── static/js/{interface,system,application,scan-task}-management.js
├── static/css/interface-management.css
scripts/migrate_apstack_docs.py  # APStack MkDocs 树 → OtterWiki 内容仓库的迁移器（CLI）
docs/structured-navigation.md    # 结构化导航规则说明（中文）
docs/multi-space-upgrade.md      # 多空间部署/升级 runbook（中文）
docs/windows*-migration.md       # Windows 环境迁移指南（中文）
tests/                       # pytest 测试（test_spaces.py / test_space_migration.py / test_document_font_size.py 等为定制功能测试）
frontend/src/                # CodeMirror 6 编辑器源码 → otterwiki/static/js/cm6-bundle.min.js
app-data/                    # 本地运行时数据（勿动）
settings.cfg[.skeleton]      # 本地配置（skeleton 是模板，勿动前者）
```

## 5. 核心架构要点

- **模块级单例**：`from otterwiki.server import app, db, storage` 在 import 时完成初始化。测试共享同一组单例，**任何测试修改 `app.config` 都会泄漏到后续测试**——`tests/conftest.py` 的 `create_app` fixture 已做 config 快照恢复，新增测试必须使用该 fixture 而不是自行改配置。
- **内容即 git**：页面读写全部走 `gitstorage.GitStorage`；页面名会 sanitize（去掉 `?$.#\` 与尾部斜杠），默认全部小写存储，页名大小写由首个标题决定（`RETAIN_PAGE_NAME_CASE` 可改变行为）。
- **渲染安全**：`renderer.py` 对输出 HTML 做 allowlist 消毒（`clean_html`），新增 HTML 属性/标签支持时必须同步维护 allowlist，否则会被过滤或产生安全漏洞。
- **渲染隔离**：每次 Markdown 解析创建独立渲染器；内置嵌入插件的页面、附件及表格状态由 `render_context.py` 按请求隔离。CLI/后台批处理应在每份文档的页面上下文钩子、渲染及资源收集外包裹 `render_context()`。
- **插件钩子**：`renderer_markdown_preprocess`、`renderer_html_postprocess` 等钩子由 `chain_hooks` 调用；新增钩子需在 `OtterWikiPluginSpec` 中定义 hookspec。
- **中文 JSON**：`server.py` 中 `app.json.ensure_ascii = False` 是本分支为中文 flash 消息添加的，不要改回默认值。

## 6. 本分支定制功能（重点，改动前先读对应模块 docstring）

### 6.1 接口管理工作台（`interface_management.py`）
- 管理员专属；导航栏右侧「接口管理」按钮进入，工作台路由 `/-/interface/<tab>`。
- 通过 `APSTACK_API_BASE_URL`（settings.cfg 配置）代理调用外部系统 `/idp/api/*` 接口；`APSTACK_API_TIMEOUT` 控制超时。
- 所有外部响应必须经 `normalise_*()` 白名单归一化，异常统一抛 `InterfaceAPIError`（用户可见中文消息）；响应大小限制 2MB。
- 标签页：工作台 / 应用管理 / 扫描任务 / 应用快照 / 模块快照 / 交易资产目录 / 资产关系分析 / 审计问题 / 版本对比 / API网关。
- 前端交互在 `static/js/*-management.js`，配合 `templates/interface_management.html`；表格用 simple-datatables，页面跳转等交互细节改动需同步更新 `tests/test_interface_management.py` 的断言。

### 6.2 结构化文档导航（`structured_navigation.py`）
- 仅当某个一级命名空间同时存在 `.portal.yml` 和 `.idp/.model.yaml` 时自动启用，普通 wiki 页面不受影响。
- 规则链路：`.idp/.model.yaml`（领域/模块/组件）→ `.portal.yml`（组件 code → 文档目录）→ `.sidebar.json`（标题/顺序/嵌套/隐藏，回退顺序含 `.sidebar.zh-cn.json`、`.sidebar.zh_CN.json`、`.sidebar.json.bak`）→ Markdown frontmatter（`title`/`nav_weight`/可见性）。
- 返回结构与 `SidebarPageIndex` 兼容；有 lru 缓存，改动规则后须调用 `clear_structured_navigation_cache()`。详细规则见 `docs/structured-navigation.md`。

### 6.3 文档导入 / 重置（`document_import.py` + `scripts/migrate_apstack_docs.py`）
- 管理员页面触发「重置并导入」：支持 ZIP 上传或服务器文件夹，异步执行，需输入确认文本 `RESET APSTACK`。
- 迁移器把 APStack MkDocs 树迁入 `apstack6` 命名空间：展开 `include()` 指令、重写本地链接为 OtterWiki URL、处理附件/图片。
- 改动迁移逻辑时必须更新 `tests/test_document_import.py`；注意 Windows 兼容（本分支有专门的 Windows 修复历史，路径处理用 `PurePosixPath` 语义，勿引入 Unix 假设）。

### 6.4 导航编辑器（`navigation_editor.py`）
- 路由 `/-/admin/navigation`，读写结构化导航配置；约束：ID 须匹配 `^[\w:./-]{1,500}$`，上限 20 个标签页、5000 个节点。

### 6.5 多空间与用户组（spaces.py + migrations.py + gitstorage.SpaceStorageProxy）
- 空间 = 独立 git 仓库（SPACES_ROOT/空间ID/repository，默认 REPOSITORY 同级 spaces 目录）；默认空间沿用原仓库，URL 不变。
- 新空间 URL /-/s/<slug>/...：SpacePrefixMiddleware 重写 PATH_INFO 并设置 SCRIPT_NAME，url_for 自动补前缀；仅重写数据库中已存在的 slug。
- 权限门禁 space_access_gate：阅读 = 登录 + 全局读取条件 + 组成员并集授权；管理员绕过；未授权/归档统一 404；任何内容加载前强制执行。
- 页面级读写仍走全局 storage 单例（SpaceStorageProxy 按 g.space 委派）；后台/CLI 任务用 spaces.current_storage() 并显式传 storage（如 document_import）。
- 迁移 flask --app otterwiki.server db upgrade：幂等；重复运行不重新添加被移除的组成员；runbook 见 docs/multi-space-upgrade.md。
- 每个迁移版本的结构、数据和版本记录原子提交，SQLite 显式开启事务；v3 清理孤立权限关系，不重新播种。旧表列变更只由迁移执行，不能依赖 Web 启动补齐。
- PROXY_HEADER 代理认证与多空间权限不兼容：启动检测到即告警，不提供静默绕过。

## 7. 开发与测试命令

```bash
# 环境准备（一次）
make venv                    # 创建 venv 并安装 [dev] 依赖与 pre-commit
cp settings.cfg.skeleton settings.cfg   # 然后编辑本地配置（REPOSITORY 等）

# 运行
make run                     # 开发服务器（需 settings.cfg）
make debug                   # 调试模式
make cli ARGS="routes"       # flask CLI

# 测试（务必带上 OTTERWIKI_SETTINGS=""，避免读到本地 settings.cfg）
make test                    # = OTTERWIKI_SETTINGS="" pytest tests docs/plugin_examples/tests
make coverage                # 覆盖率报告（HTML 输出到 coverage_html/）

# 前端（仅当修改 frontend/src/ 时）
make frontend                # cd frontend && npm install && node esbuild.config.mjs
                             # 产物: otterwiki/static/js/cm6-bundle.min.js（需提交产物）

# 代码风格
make black                   # black --line-length 79（不强制双引号）
```

运行单个测试示例：
```bash
OTTERWIKI_SETTINGS="" venv/bin/pytest tests/test_interface_management.py -x -q
```

## 8. 提交规范

- 使用 conventional commit：`feat:` / `fix:` / `perf:` / `refactor:` / `docs:` / `test:` / `chore:`。
- 参考本分支历史：`feat: 接口管理新增系统、应用与扫描任务管理`、`fix: 修复win导入后无法删除备份`。
- CHANGELOG 由 `git-changelog` 从 commit 自动生成；pre-commit 钩子已配置，提交前会自动检查。
- 不要直接推送到 `upstream`；PR 目标为本仓库 `main`。

## 9. 常见陷阱（代理必须注意）

1. **测试配置泄漏**：`app.config`/`db`/`storage` 是单例。测试里改配置前先看 `tests/conftest.py` 的 `create_app` 快照机制；不要在新测试里直接改全局配置而不恢复。
2. **CSRF**：全局开启。测试里发 POST 用 `CSRFTestClient`（conftest 提供）自动注入 token；新增表单要包含 `csrf_token`。
3. **HTML 消毒**：所有用户输入渲染前经过 `clean_html`。给 renderer 增加新语法/嵌入插件（`renderer_plugins.py`）时，确认输出能通过 allowlist，否则功能静默失效。
4. **页面名与大小写**：页面名会被 sanitize 且默认小写；涉及中文页名、`RETAIN_PAGE_NAME_CASE` 的测试必须覆盖。
5. **接口管理的外部依赖**：外部 IDP API 不可用时工作台要优雅降级（显示中文错误而非 500）；测试用 monkeypatch 模拟 `request_api_json`，不要真连外部服务。
6. **结构化导航缓存**：规则文件变更后缓存不会自动失效，代码路径需显式清缓存；测试需构造完整 `.portal.yml`/`.model.yaml` fixture。
7. **Windows 兼容**：本分支维护了 Windows 运行/迁移路径，文件操作避免依赖 Unix-only 语义（如 `os.chmod` 特定 mode、符号链接假设），路径拼接用 `PurePosixPath`/`join_path`。
8. **JSON 中文**：直接 `json.dumps` 中文给前端时记得 `ensure_ascii=False`（app.json 已全局设置，但模块内独立 dumps 需自行处理）。
9. **静态资源**：修改 `otterwiki/static/js/*.js`（非 cm6-bundle）直接生效；修改 `frontend/src/` 必须重新构建并提交 `cm6-bundle.min.js` 产物。

## 10. 安全须知

- 本仓库有 `SECURITY.md`；`otterwiki/security_check.py` 提供管理员安全自检页面（`/-/housekeeping/security-check`）。
- 渲染器对 URL 协议有白名单（`javascript:`、实体编码协议等会被拦截，见 renderer 内 `escape_url`/`clean_html` 逻辑与相关测试 `tests/test_renderer.py`）。
- `document_import.py` 对 ZIP 做路径穿越、符号链接、总大小校验（`ImportLimits`），改动导入逻辑不得放宽这些校验。
- 外部接口响应必须大小受限、格式归一化、异常转中文提示，不得把外部返回内容直接渲染为 HTML。

## 11. 标准变更工作流（给代理的执行清单）

1. 阅读相关模块 docstring 与对应 `tests/test_*.py`，理解现有行为。
2. 修改代码（遵守第 2 节铁律：中文文案、79 列 black、modeline）。
3. 新增/更新 pytest 测试（用 `create_app` fixture、`CSRFTestClient`）。
4. `make black` 格式化 + `make test` 全量通过。
5. 若改了前端源码，执行 `make frontend` 并提交产物。
6. 按 conventional commit 提交，中文 commit message。
7. 若涉及新配置项：同步更新 `settings.cfg.skeleton` 注释说明（中英均可，已有 APStack 配置为中文注释先例）。
