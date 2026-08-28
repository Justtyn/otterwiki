# Windows CMD：重新导入 APStack 文档

本文适用于在 Windows 的 **命令提示符（Command Prompt / CMD）** 中，通过 `scripts\migrate_apstack_docs.py` 将 APStack 文档迁移到源码运行的 OtterWiki。

> CMD 和 PowerShell 的语法不同。CMD 中不要使用 `$Python`、`& $Python` 或 PowerShell 的反引号续行。CMD 变量使用 `%变量名%`。

## 一、打开 CMD 并进入项目目录

在 Windows Terminal 中选择“命令提示符”，或者按 `Win+R`，输入 `cmd` 后回车。

进入 OtterWiki 源码根目录。`/d` 表示同时切换盘符和目录：

```bat
cd /d D:\Code\Project\otterwiki\otterwiki
```

确认当前位置正确：

```bat
dir scripts\migrate_apstack_docs.py
```

如果提示找不到文件，说明当前目录不是包含 `scripts`、`otterwiki` 和 `app-data` 的项目根目录。

## 二、设置源文档和目标内容库

把第一行替换成自己实际的 APStack 文档路径：

```bat
set "DOC_SOURCE=D:\Documents\apstack-doc"
set "WIKI_REPO=%CD%\app-data\repository"
```

`DOC_SOURCE` 必须指向包含 `docs` 文件夹的 `apstack-doc` 根目录，而不是直接指向 `docs`：

```text
D:\Documents\apstack-doc\
├─ docs\
├─ nav\                 可选
├─ img\                 可选
├─ README.md            可选
└─ .portal.yml          可选
```

检查两个路径：

```bat
if not exist "%DOC_SOURCE%\docs\" echo [错误] 源目录中没有 docs 文件夹
if not exist "%WIKI_REPO%\.git\" echo [错误] 目标目录中没有 .git
echo 源文档：%DOC_SOURCE%
echo 目标内容库：%WIKI_REPO%
```

如果出现任何错误，不要继续执行删除或迁移命令。

## 三、确认 Python 命令

优先检查：

```bat
python --version
```

如果能够显示 Python 3 版本，后续使用 `python` 即可。

如果提示找不到 `python`，尝试 Windows Python Launcher：

```bat
py -3 --version
```

如果项目使用虚拟环境，也可以直接检查：

```bat
venv\Scripts\python.exe --version
```

后面的示例默认使用 `python`。如果本机只能使用 `py -3` 或虚拟环境，请按下面方式替换：

```bat
py -3 scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%"
```

或者：

```bat
venv\Scripts\python.exe scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%"
```

## 四、停止 OtterWiki

在运行 OtterWiki 的终端窗口中按 `Ctrl+C` 停止服务。迁移期间不要在网页中编辑 Wiki。

## 五、备份当前 Wiki 内容库

查看当前状态：

```bat
git -C "%WIKI_REPO%" status --short
```

如果有尚未提交的内容，确认这些内容都需要保留后再提交：

```bat
git -C "%WIKI_REPO%" add -A
git -C "%WIKI_REPO%" commit -m "重新导入 APStack 文档前备份"
```

创建一个备份分支：

```bat
git -C "%WIKI_REPO%" branch backup-before-apstack-import
```

如果提示该分支已经存在，可换一个名字，例如：

```bat
git -C "%WIKI_REPO%" branch backup-before-apstack-import-2
```

只要保留内容库中的 `.git`，就可以从备份分支找回迁移前的文件。

## 六、清空内容库并保留 `.git`

> 警告：本节会删除 `app-data\repository` 中的 Wiki 页面和附件。必须先确认 `%WIKI_REPO%` 路径正确并完成上一节的 Git 备份。不要删除 `repository` 文件夹本身，也不要删除 `.git`。

再次执行安全检查：

```bat
if not exist "%WIKI_REPO%\.git\" exit /b 1
if /i not "%WIKI_REPO:~-10%"=="repository" exit /b 1
```

先预览 Git 已跟踪的文件：

```bat
git -C "%WIKI_REPO%" ls-files
```

再预览未跟踪及被忽略、即将被清理的文件：

```bat
git -C "%WIKI_REPO%" clean -ndx
```

确认列表后，要求手工输入 `DELETE`：

```bat
set "CONFIRM="
set /p "CONFIRM=确认清空 Wiki 内容并保留 .git，请输入 DELETE："
if /i not "%CONFIRM%"=="DELETE" exit /b 1
```

正式删除 Git 已跟踪文件，并清理未跟踪文件：

```bat
git -C "%WIKI_REPO%" rm -r .
git -C "%WIKI_REPO%" clean -fdx
```

检查结果：

```bat
dir /a "%WIKI_REPO%"
git -C "%WIKI_REPO%" status --short
```

目录中应保留 `.git`。Git 状态显示大量删除记录是正常现象，新导入的文件会和这些删除一起进入下一次提交。

### 仅替换 APStack6 的可选方式

如果内容库中还有其他 Wiki 页面需要保留，不要执行上面的全库清理。只删除 APStack6 命名空间：

```bat
if exist "%WIKI_REPO%\apstack6\" rmdir /s /q "%WIKI_REPO%\apstack6"
if exist "%WIKI_REPO%\apstack6.md" del /f /q "%WIKI_REPO%\apstack6.md"
```

## 七、预检查迁移

清空旧内容后，本次属于全新迁移，不需要 `--refresh`：

```bat
python scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%"
```

正常输出类似：

```text
源页面：120
源附件：48
展开 include：0
重写链接：0
未解析引用：0
模式：仅检查
```

如果提示目标已经存在 `apstack6.md` 或 `apstack6`，说明旧内容没有清理干净。也可以使用 `--refresh` 迁移，但 `--refresh` 不会删除残留的旧文件。

## 八、正式迁移

预检查通过后执行：

```bat
python scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%" --apply
```

不要在 CMD 中写成下面的 PowerShell 形式：

```text
& $Python ".\scripts\migrate_apstack_docs.py" ...
```

CMD 可以使用插入符号 `^` 进行续行，但最不容易出错的方式是像上面一样写成一整行。

## 九、校验迁移结果

```bat
python scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%" --verify
```

正常输出类似：

```text
目标文件映射：168/168 完整
附件 SHA-256：48/48 一致
迁移后绝对链接：320/320 可解析
```

检查 Git 差异：

```bat
git -C "%WIKI_REPO%" status --short
git -C "%WIKI_REPO%" diff --stat
git -C "%WIKI_REPO%" diff -- home.md
```

脚本会重新生成 `home.md`。如果想保留原来的自定义首页，可在提交前从备份分支恢复：

```bat
git -C "%WIKI_REPO%" restore --source backup-before-apstack-import -- home.md
```

## 十、提交迁移结果

确认文件和页面正确后执行：

```bat
git -C "%WIKI_REPO%" add -A
git -C "%WIKI_REPO%" commit -m "重新导入 APStack6 产品文档"
git -C "%WIKI_REPO%" log -3 --oneline
```

## 十一、重新启动源码服务

### 1. 首次运行时创建虚拟环境

如果项目根目录中已经有可用的 `venv`，可以跳过这一步。否则执行：

```bat
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip wheel
venv\Scripts\python.exe -m pip install -e .
```

项目要求 Python 3.11 或更高版本。如果没有 Python 3.12，也可以使用已经安装的 3.11 或更高版本：

```bat
py -0p
py -3.11 -m venv venv
```

### 2. 创建并修改 `settings.cfg`

如果项目根目录还没有 `settings.cfg`：

```bat
copy settings.cfg.skeleton settings.cfg
```

生成一个随机密钥：

```bat
venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

复制输出，然后用记事本打开配置：

```bat
notepad settings.cfg
```

至少确认以下三项。Windows 路径建议使用正斜杠：

```python
SECRET_KEY = '粘贴刚才生成的随机密钥'
REPOSITORY = 'D:/Code/Project/otterwiki/otterwiki/app-data/repository'
SQLALCHEMY_DATABASE_URI = 'sqlite:///D:/Code/Project/otterwiki/otterwiki/app-data/db.sqlite'
```

`REPOSITORY` 必须指向已经完成迁移并且包含 `.git` 的内容库。`SQLALCHEMY_DATABASE_URI` 指向用户数据库；如果之前已经存在 `app-data/db.sqlite`，继续使用这个文件即可。

### 3. 启动 OtterWiki

确保当前 CMD 位于项目根目录，然后执行：

```bat
set "FLASK_APP=otterwiki.server"
set "OTTERWIKI_SETTINGS=%CD%\settings.cfg"
venv\Scripts\python.exe -m flask run --host 127.0.0.1 --port 8081
```

看到类似下面的输出即表示启动成功：

```text
* Running on http://127.0.0.1:8081
```

浏览器访问：

```text
http://127.0.0.1:8081/apstack6
```

迁移报告位于：

```text
http://127.0.0.1:8081/apstack6/迁移报告
```

保持 CMD 窗口打开，关闭窗口或按 `Ctrl+C` 会停止服务。

如果需要让同一局域网中的其他电脑访问，可以将启动参数改成 `--host 0.0.0.0`，并在 Windows 防火墙中只对可信网络开放端口。仅本机使用时应保留 `127.0.0.1`。

## 十二、以后增量更新

后续只是新增或修改文档时，不需要再次清空内容库：

```bat
python scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%" --refresh --apply
python scripts\migrate_apstack_docs.py "%DOC_SOURCE%" "%WIKI_REPO%" --verify
```

最后检查 Git 差异并提交。

`--refresh` 会覆盖映射到的现有文件并加入新文件，但不会自动删除已经从源文档中移除的旧文件。源文件删除或改名后，应根据 Git 差异人工处理目标库中的残留文件。
