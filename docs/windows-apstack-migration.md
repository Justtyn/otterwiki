# Windows 本地源码环境：重新导入 APStack 文档

本文适用于以下场景：

- 在 Windows 上通过源码运行 OtterWiki；
- OtterWiki 内容库位于项目的 `app-data/repository`；
- 希望清空原有 Wiki 内容，但保留内容库的 Git 历史；
- 使用 `scripts/migrate_apstack_docs.py` 将一个结构相同的 `apstack-doc` 文档目录重新迁移进去。

下面的命令均在 **PowerShell** 中执行。正式删除前，请先完成 Git 备份并仔细核对路径。

## 一、迁移脚本处理什么

脚本的第一个参数必须是 `apstack-doc` 的**根目录**，不能直接传入其中的 `docs` 目录。

源目录通常应具有以下结构：

```text
apstack-doc/
├─ docs/
│  ├─ index.md
│  ├─ guide/
│  │  ├─ install.md
│  │  └─ images/
│  │     └─ install.png
│  └─ .sidebar.json              # 如果原项目使用结构化导航
├─ nav/
│  └─ docs/                      # 可选
├─ img/                          # 可选
├─ README.md                     # 可选
├─ APStack错误码总结.md          # 可选
├─ .portal.yml                   # 可选
└─ .idp/
   └─ .model.yaml                # 可选
```

主要映射关系如下：

| 源文件 | OtterWiki 内容库中的目标文件 |
| --- | --- |
| `docs/index.md` | `apstack6.md` |
| `docs/guide/install.md` | `apstack6/guide/install.md` |
| `README.md` | `apstack6/项目说明.md` |
| `nav/docs/...` | `apstack6/导航资料/...` |

脚本会转换 Markdown 页面、展开 `include(...)`、重写本地链接，并复制图片及其他附件。PDF、Word 等文件只会作为附件复制，不会自动转换为 Markdown 页面。

## 二、停止 OtterWiki

先在正在运行 OtterWiki 的 PowerShell 窗口中按 `Ctrl+C` 停止服务。迁移期间不要在网页中编辑内容，否则可能与文件迁移或 Git 提交发生冲突。

## 三、进入 OtterWiki 项目并设置路径

进入本地源码目录，例如：

```powershell
Set-Location "D:\workspace\otterwiki"
```

设置项目、源文档和目标内容库路径。请将文档路径替换成自己的实际位置：

```powershell
$ProjectRoot = (Get-Location).Path
$DocSource = (Resolve-Path "D:\documents\apstack-doc").Path
$WikiRepo = [System.IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot "app-data\repository")
)
$Python = "python"
```

如果 `python` 命令不可用，可以改成 Windows Python Launcher：

```powershell
$Python = "py"
```

如果只想使用项目虚拟环境中的 Python，则改成：

```powershell
$Python = (Resolve-Path ".\venv\Scripts\python.exe").Path
```

## 四、执行删除前的强制路径检查

以下检查用于避免误删错误目录：

```powershell
if (-not (Test-Path -LiteralPath ".\scripts\migrate_apstack_docs.py" -PathType Leaf)) {
    throw "当前目录不是 OtterWiki 项目根目录。"
}

if (-not (Test-Path -LiteralPath (Join-Path $DocSource "docs") -PathType Container)) {
    throw "源目录中没有 docs 文件夹：$DocSource"
}

if (-not (Test-Path -LiteralPath (Join-Path $WikiRepo ".git") -PathType Container)) {
    throw "目标不是 OtterWiki Git 内容库：$WikiRepo"
}

if ((Split-Path -Leaf $WikiRepo) -ne "repository") {
    throw "目标目录名不是 repository，停止执行：$WikiRepo"
}

if ($WikiRepo -eq [System.IO.Path]::GetPathRoot($WikiRepo)) {
    throw "目标不能是磁盘根目录。"
}

Write-Host "源文档：$DocSource"
Write-Host "目标内容库：$WikiRepo"
```

检查输出，确认：

- `$DocSource` 是包含 `docs` 的 `apstack-doc` 根目录；
- `$WikiRepo` 准确指向当前项目的 `app-data\repository`；
- 不要把 `$WikiRepo` 设置成 `app-data`、项目根目录或磁盘根目录。

## 五、备份当前内容库

先查看现有修改：

```powershell
git -C $WikiRepo status --short
```

如果存在未提交内容，先提交：

```powershell
$PendingChanges = git -C $WikiRepo status --porcelain
if ($PendingChanges) {
    git -C $WikiRepo add -A
    git -C $WikiRepo commit -m "重新导入 APStack 文档前备份"
}
```

再创建一个备份分支指向当前提交：

```powershell
$BackupBranch = "backup/apstack-before-clean-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
git -C $WikiRepo branch $BackupBranch
Write-Host "已创建备份分支：$BackupBranch"
```

记住输出的分支名。只要保留 `.git`，原内容就仍然可以从这个备份分支恢复。

## 六、清空内容库，但必须保留 `.git`

> 警告：下面的命令会删除 `app-data/repository` 中除 `.git` 之外的全部文件和文件夹。不要直接删除 `repository` 文件夹，也不要删除其中的 `.git`。

先只显示即将删除的项目：

```powershell
$ItemsToDelete = Get-ChildItem -LiteralPath $WikiRepo -Force |
    Where-Object { $_.Name -ne ".git" }

$ItemsToDelete | Select-Object FullName
```

确认列表中没有需要保留的文件后，输入指定确认词再删除：

```powershell
$Confirmation = Read-Host "确认删除以上内容并保留 .git？请输入 DELETE"
if ($Confirmation -ne "DELETE") {
    throw "用户取消了删除。"
}

$ItemsToDelete | Remove-Item -Recurse -Force
```

删除后再次检查：

```powershell
Get-ChildItem -LiteralPath $WikiRepo -Force
git -C $WikiRepo status --short
```

第一条命令应只看到 `.git`。第二条命令显示大量已删除文件属于正常现象，这些删除会和新导入内容一起记录到下一次 Git 提交中。

### 只替换 APStack6 内容的可选方案

如果内容库中还有其他 Wiki 页面需要保留，就不要清空整个内容库，只删除脚本管理的 APStack6 命名空间：

```powershell
Remove-Item -LiteralPath (Join-Path $WikiRepo "apstack6") -Recurse -Force
Remove-Item -LiteralPath (Join-Path $WikiRepo "apstack6.md") -Force
```

此方案会保留其他页面。脚本仍会重新生成 `home.md` 和 `apstack6/迁移报告.md`。

## 七、先执行预检查

因为旧的 `apstack6` 已经删除，本次属于全新迁移，不需要 `--refresh`：

```powershell
& $Python ".\scripts\migrate_apstack_docs.py" `
    $DocSource `
    $WikiRepo
```

成功时会看到类似输出：

```text
源页面：120
源附件：48
展开 include：0
重写链接：0
未解析引用：0
模式：仅检查
```

预检查阶段的“展开 include”和“重写链接”通常为 `0`，因为这时还没有实际转换文件。重点确认页面数、附件数是否符合预期，并确认没有出现路径冲突。

常见错误：

- `源文档目录不存在`：`$DocSource` 指错了，或把路径指向了别的目录；
- `目标不是 OtterWiki Git 内容库`：`.git` 被删除了，或者 `$WikiRepo` 路径错误；
- `目标路径冲突`：两个源文件经过小写化和非法字符清理后映射到了同一个目标路径；
- 提示 `apstack6` 已存在：旧命名空间没有清理干净；重新导入时也可以改用 `--refresh`，但它不会删除残留的旧文件。

## 八、正式迁移

预检查通过后添加 `--apply`：

```powershell
& $Python ".\scripts\migrate_apstack_docs.py" `
    $DocSource `
    $WikiRepo `
    --apply
```

脚本会：

1. 保持源文档目录不变；
2. 将 Markdown 页面迁移到 `apstack6` 命名空间；
3. 复制图片、SVG、HTML、CSS、配置和下载附件；
4. 展开支持的 `include(...)` 指令；
5. 将可识别的本地链接改写为迁移后的 OtterWiki 链接；
6. 生成 `home.md` 和 `apstack6/迁移报告.md`。

## 九、校验迁移结果

执行脚本自带的完整性校验：

```powershell
& $Python ".\scripts\migrate_apstack_docs.py" `
    $DocSource `
    $WikiRepo `
    --verify
```

正常输出类似：

```text
目标文件映射：168/168 完整
附件 SHA-256：48/48 一致
迁移后绝对链接：320/320 可解析
```

然后检查 Git 差异：

```powershell
git -C $WikiRepo status --short
git -C $WikiRepo diff --stat
git -C $WikiRepo diff -- "home.md"
```

脚本会固定重写 `home.md`。如果需要保留迁移前的自定义首页，可以在提交前从备份分支恢复它：

```powershell
git -C $WikiRepo restore --source $BackupBranch -- "home.md"
```

## 十、提交新的内容库状态

确认页面、附件和首页都正确后提交：

```powershell
git -C $WikiRepo add -A
git -C $WikiRepo commit -m "重新导入 APStack6 产品文档"
git -C $WikiRepo log -3 --oneline
```

不要跳过提交。OtterWiki 使用 Git 保存页面历史，完成迁移后提交可以让本次导入具备清晰的恢复点。

## 十一、确认源码运行配置并重新启动

检查 `settings.cfg` 中的 `REPOSITORY`。Windows 路径建议使用正斜杠，例如：

```python
REPOSITORY = 'D:/workspace/otterwiki/app-data/repository'
```

它必须和本教程中的 `$WikiRepo` 指向同一目录。

然后使用原来的源码启动方式重新启动 OtterWiki。典型的 PowerShell 启动示例如下：

```powershell
$env:FLASK_APP = "otterwiki.server"
$env:OTTERWIKI_SETTINGS = (Join-Path $ProjectRoot "settings.cfg")
& ".\venv\Scripts\flask.exe" run --host 127.0.0.1 --port 8081
```

重新启动可以清除进程中旧的结构化导航缓存。启动后访问：

```text
http://127.0.0.1:8081/apstack6
```

并检查迁移报告：

```text
http://127.0.0.1:8081/apstack6/迁移报告
```

还应抽查以下内容：

- 新增和更新的 Markdown 页面；
- 中文页面路径；
- 图片、SVG 和下载附件；
- 页面之间的相对链接；
- 左侧结构化导航；
- `apstack6/迁移报告` 中的未解析引用。

## 十二、出现问题时恢复

先停止 OtterWiki，然后查看提交记录：

```powershell
git -C $WikiRepo log --oneline --decorate -10
```

如果已经提交了错误的导入，可以反向提交刚才的迁移提交：

```powershell
git -C $WikiRepo revert <迁移提交哈希>
```

也可以通过第五步创建的 `$BackupBranch` 找到迁移前的全部内容。恢复后重新启动 OtterWiki。

## 后续增量更新

完成首次全量重建后，今后只是增加或修改文档时，不需要再次清空内容库。使用 `--refresh --apply` 即可：

```powershell
& $Python ".\scripts\migrate_apstack_docs.py" `
    $DocSource `
    $WikiRepo `
    --refresh `
    --apply
```

随后再次运行 `--verify`、检查 `git diff` 并提交。

注意：`--refresh` 会覆盖脚本映射到的同名文件并加入新文件，但不会自动删除已经从源文档中移除的旧目标文件。遇到源文件删除或改名时，应根据 Git 差异人工确认并删除残留文件。
