# 结构化产品文档导航

OtterWiki 可从已迁移的产品文档规则中构建导航，不需要把目录层级硬编码到应用配置中。普通 Wiki 页面仍使用原有页面索引；只有某个一级命名空间同时存在以下文件时，才自动启用结构化导航：

```text
<命名空间>/
├── .portal.yml
└── .idp/
    └── .model.yaml
```

## 规则链路

| 规则 | 用途 | 结果 |
| --- | --- | --- |
| `.idp/.model.yaml` | 定义领域、模块和组件 | 左侧导航的模块与组件层级 |
| `.portal.yml` | 将组件 `code` 映射到原 `docs/...` 路径 | 组件与迁移后目录的关联 |
| `.sidebar.json` | 定义目录内标题、顺序、嵌套和隐藏项 | 组件内部的文档树 |
| Markdown frontmatter | 定义页面标题、权重和可见性 | 未显式配置页面的展示方式 |

目录规则按以下顺序查找，找到第一份有效规则后停止：

1. `.sidebar.json`
2. `.sidebar.zh-cn.json`
3. `.sidebar.zh_CN.json`
4. `.sidebar.json.bak`（兼容旧文档库）

`.sidebar.json` 支持 `title`、`path`、`children` 和 `hide`。规则中没有列出的页面会自动追加，并按 `nav_weight`、标题、文件名排序；设置为隐藏的规则项不会被自动发现再次加入。

```json
[
  {
    "title": "批量",
    "path": "aps-batch",
    "children": [
      {"title": "快速入门", "path": "aps-batch/start"},
      {"title": "使用指南", "path": "aps-batch/guide"}
    ]
  }
]
```

Markdown 页面支持以下 frontmatter：

```yaml
---
title: 批量组件集成-批转联
nav_weight: 10
hide: false
---
```

`order` 可作为 `nav_weight` 的别名，`hidden` 可作为 `hide` 的别名。frontmatter 的 `title` 同时用于页面标题和导航标题；显式 `.sidebar.json` 标题的优先级更高。

## 显示行为

- 顶部自动显示“产品手册 / 产品组件 / 参考指南”，并根据当前路径高亮。
- 产品组件使用“模块 → 组件 → 目录规则 → 页面”的层级。
- 左侧树使用逐级编号，首次构建即包含当前分类的完整子树；折叠目录不需要进入页面后才加载。
- 结构化文档的正文标题和右侧页内目录使用相同编号，锚点保持不变。
- 当规则 `path` 指向目录时，按 `index/readme → sidebar 首项 → 当前目录页面 → 首个有效后代页面` 寻找实际落地页。
- JavaDoc、静态 FAQ 等目录可落到 `index.html`；只有附件而没有正文的目录不进入导航，避免产生死节点。
- 规则损坏或缺失时记录警告并回退到普通 Wiki 页面索引。

## 缓存与状态

- 完整导航树按“内容仓库路径 + Git 提交版本 + 页面大小写配置 + 文档分类”缓存。
- 同一提交下切换页面只复制缓存树并重新标记当前路径，不重新扫描目录，因此不同页面看到的树结构和编号保持一致。
- 内容仓库产生新提交后缓存键自动变化，下一次请求会构建包含最新页面的新树。
- 浏览器使用稳定节点键在 `sessionStorage` 中保存展开/折叠状态；当前页面的所有祖先节点始终强制展开，避免历史折叠状态遮住当前页。

## APStack6 迁移

迁移脚本会将根规则复制到 `apstack6` 命名空间，保留各级 `.sidebar.json(.bak)`，并将原 `docs/...` 路径转换为迁移后的 Wiki 路径：

```shell
venv/bin/python scripts/migrate_apstack_docs.py \
  /Users/justyn/Desktop/Code/ncbs-apstack6-docs-master \
  app-data/repository --apply --refresh

venv/bin/python scripts/migrate_apstack_docs.py \
  /Users/justyn/Desktop/Code/ncbs-apstack6-docs-master \
  app-data/repository --verify
```

源仓库中只有标题或内容不足三行的占位页会被补充为有效目录入口，防止 OtterWiki 的维护工具将仍被导航引用的页面判定为空页面。
