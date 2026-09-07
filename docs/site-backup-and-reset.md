# 网站备份、导入与初始化

适用于已完成数据库升级的 SQLite 文件数据库部署。网站使用单副本、单 Web 进程；
数据库路径必须是绝对路径，各数据目录不能互相包含。备份和恢复不访问远程 Git 平台。

## 在管理页导出与导入

进入「设置 → 网站备份与恢复」。导出会短暂阻止新请求，等待现有请求结束，
并检查文档导入和各空间 Git 同步的执行锁。任务仍在运行时，稍后重试。

备份包含：

- 所有空间的文档、附件、未提交文件、分支及 Git 历史。
- 账号、密码哈希、组成员、空间权限、草稿和网站设置。
- 远程仓库配置及凭据。导入时使用目标网站 SECRET_KEY 重新加密。

备份不包含运行缓存、历史后台任务、插件程序、settings.cfg、环境变量、
服务器 SECRET_KEY 和 Git 钩子。目标服务器的数据库和存储路径保持原配置。
Git 本地配置在导入时重建，SSH known_hosts 等宿主环境文件需要另行配置。
**ZIP 未加密，含敏感凭据，请使用受控存储保存和传输。**

导入只接受本功能导出的同格式、同数据库结构和迁移版本备份。先备份当前站点，
选择 ZIP 并输入 `RESTORE OTTERWIKI`。上传通过完整校验后进入待导入状态，
页面可取消。然后停止所有实例，再启动单个实例；启动时自动导入。
不要滚动更新，避免旧实例在新实例替换数据时仍处理请求。
上传后的新编辑会被备份覆盖，建议在维护窗口立即停站启动。

导入后用备份中的账号登录，旧会话失效。若迁移至其他域名，应检查恢复的网站域名、
邮件、接口服务地址以及 Git SSH 主机信任配置。

## 初始化脚本

停止所有网站进程后，在项目目录中先查看操作范围（此命令不修改数据）：

```bash
venv/bin/python -m otterwiki.site_backup --settings /path/to/settings.cfg reset
```

确认已有可用备份后执行：

```bash
venv/bin/python -m otterwiki.site_backup --settings /path/to/settings.cfg reset --confirm "RESET OTTERWIKI"
```

Windows 使用 `venv\Scripts\python.exe`。已安装的容器镜像使用
`/opt/venv/bin/python`。在 TKE 中先将 Deployment 缩容为 0，再用相同业务镜像、
相同配置 Secret 和 PVC 运行一次维护 Job，覆盖 command 为上述 Python 命令；
等待 Job 成功后再恢复单副本。维护 Job 必须与应用使用相同的卷访问身份。

脚本清空所有账号、用户组、权限、草稿、缓存、远程仓库配置和后台任务，
替换默认仓库、整个 SPACES_ROOT 及 DOCUMENT_IMPORT_TASK_ROOT，移除旧 Git 历史，
保留当前数据库表结构和迁移版本。默认网站设置以数据库偏好保存，覆盖旧站点偏好；
不改写部署配置文件、路径和 SECRET_KEY。插件文件与维护目录外的人工备份不删除。
重新启动后生成初始首页，第一个注册用户成为管理员。

## 停站导出与手工恢复

```bash
venv/bin/python -m otterwiki.site_backup --settings /path/to/settings.cfg export --archive /backup/wiki.zip
venv/bin/python -m otterwiki.site_backup --settings /path/to/settings.cfg restore --archive /backup/wiki.zip --confirm "RESTORE OTTERWIKI"
```

也可手工应用管理页上传的待导入备份：

```bash
venv/bin/python -m otterwiki.site_backup --settings /path/to/settings.cfg apply-pending --confirm "RESTORE OTTERWIKI"
```

这些命令都要求停站；脚本不会代替运维平台停止进程。导出不会覆盖已有文件。
初始化前如有待导入备份，先在管理页取消，防止重启时意外恢复旧站点。

## 故障恢复与限制

中文 Windows 在重置后的首次启动中，如果出现
`UnicodeDecodeError: 'gbk' codec can't decode byte 0x81 in position 28`，
请更新 `otterwiki/server.py`，确保读取 `initial_home.md` 的 `open()`
显式指定 `encoding="utf-8"`。重置会重新创建中文首页，因此会触发旧代码中
此前未执行的模板读取。无需再次重置或删除数据库。

暂时无法更新代码时，可以为启动进程设置环境变量 `PYTHONUTF8=1`，
或在 Python 的 `-m flask` 之前添加 `-X utf8`。例如在 Windows 项目目录中：

```cmd
.venv\Scripts\python.exe -X utf8 -m flask --app otterwiki.server run --host 127.0.0.1 --port 8081
```

恢复先在临时目录完成 ZIP、数据库结构、外键和 Git 对象校验，再准备全部新数据。
仓库和数据库在各自文件系统内通过目录或文件重命名切换。切换日志保存在
SITE_BACKUP_ROOT/operation.json；中途异常回滚，进程中断后下一次启动先恢复现场。
无法恢复时阻止启动，不用空数据覆盖故障现场。不要在日志仍存在时手工删除临时目录。
磁盘应同时容纳上传 ZIP、解压内容、新数据和切换期间的旧数据。

待导入文件位于 SITE_BACKUP_ROOT/pending.zip。若导入校验失败阻止启动，保持停站，
将 pending.zip 移到安全备份目录后再启动原站点；如果还有 operation.json，
先保留完整现场并排查权限、磁盘空间和配置路径是否变化。

默认上传上限 2 GiB，解压上限 8 GiB，文件数上限 200000，可在配置文件中调整
SITE_BACKUP_MAX_ARCHIVE_SIZE、SITE_BACKUP_MAX_EXTRACTED_SIZE、SITE_BACKUP_MAX_FILES。
SITE_BACKUP_ROOT 默认在 REPOSITORY 同级的 `.otterwiki-site`，必须持久化，
且与数据库、仓库、空间和导入任务目录互不包含。
大站点建议停站导出，避免 HTTP 超时；反向代理的上传限制和等待超时需匹配站点规模。
不支持符号链接、Git worktree、外部对象引用或自定义数据库视图与触发器。
