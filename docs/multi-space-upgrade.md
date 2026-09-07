# 多空间部署与升级指南

适用于内网 Docker/TKE。构建机不需要 Python；所有迁移命令使用新应用镜像内的
`/opt/venv/bin/python`。本文只提供操作示例，不会自动操作集群。

当前容器入口会在每次启动 Web 服务前自动执行数据库迁移；已经应用的版本会跳过，
迁移失败则容器停止。单副本、`Recreate` 发布可以使用这一自动流程。生产升级仍应
先停服并完成备份；需要明确分离迁移与应用启动时，继续使用本文的独立 Job。

## 数据与权限

默认空间沿用 `REPOSITORY`，新空间位于 `SPACES_ROOT/<空间ID>/repository`。
必须一起持久化、备份 SQLite 数据库、默认仓库、新空间仓库和所需附件。
示例假定它们分别位于 `/app-data/db.sqlite`、`/app-data/repository` 和
`/app-data/spaces`，配置位于 Secret 挂载的 `/config/settings.cfg`。
如实际配置不同，请同步修改备份路径和 Job 挂载。不要只持久化 `spaces`。

- 页面需要内置账号登录、全局阅读资格和用户组授权；多组授权取并集。
- 全局管理员可以访问归档空间。普通用户在网页和 Git HTTP 均不能访问归档空间。
- v1 为升级前具备阅读资格的本地用户建立默认组；v2 补齐历史草稿空间。
- v4 创建异步导入任务表；v5 创建按空间绑定的 Git 仓库与同步任务表。
- v3 清理孤立成员及授权关系，并兼容早期草稿结构；不会重新授予已撤销的权限。
- 已经被复用的用户 ID 无法仅凭现有关系判断原归属。如果旧版本曾删除用户并
  创建新账号，请在管理界面核对这些新账号的所属组。
- 不支持 `PROXY_HEADER` 代理登录。升级前应使用内置管理员账号验证登录。

## 1. 记录环境并停止业务写入

下面的命令在装有 kubectl 且能访问集群的终端执行，不是在应用容器内执行。
先核对命名空间、Deployment、实际 Pod、Secret、PVC 和应用镜像标签：

```bash
export WIKI_NS=newcore-dev-ns
export WIKI_DEPLOY=otterwiki
export WIKI_BACKUP="$PWD/otterwiki-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$WIKI_BACKUP"
kubectl -n "$WIKI_NS" get deploy "$WIKI_DEPLOY" -o yaml > "$WIKI_BACKUP/deployment.yaml"
kubectl -n "$WIKI_NS" get pods -l app=otterwiki -o wide
# 将下面的值替换成上一步实际显示的 Pod 名称
export WIKI_POD=替换为当前Pod名称
```

通过平台维护入口暂停访问，暂停外部 Git 同步/推送及新导入提交，等待已接受的导入任务结束，确认没有写入。
**此时保持当前 Pod 存活，尤其是 emptyDir 部署：先缩容到 0 或重建 Pod，
会直接删除临时卷，之后无法再备份。** 同时暂停会自动恢复副本数的发布流水线/HPA。

## 2. 在当前 Pod 删除前备份并验证

以下示例要求镜像有 tar，备份终端有 Bash、tar、sha256sum。SQLite 使用在线备份
接口生成完整数据库文件，避免只复制主文件而遗漏 WAL。配置中的数据库若不是
`/app-data/db.sqlite`，须替换命令中的路径。

```bash
kubectl -n "$WIKI_NS" exec "$WIKI_POD" -- /opt/venv/bin/python -c '
import sqlite3
src = sqlite3.connect("file:/app-data/db.sqlite?mode=ro", uri=True)
dst = sqlite3.connect("/tmp/otterwiki-backup.sqlite")
src.backup(dst)
assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
dst.close()
src.close()
'
# exec 不加 -t，避免二进制归档被终端处理。
kubectl -n "$WIKI_NS" exec "$WIKI_POD" -- tar -C /app-data -czf - . > "$WIKI_BACKUP/app-data.tar.gz"
kubectl -n "$WIKI_NS" cp "$WIKI_POD:/tmp/otterwiki-backup.sqlite" "$WIKI_BACKUP/db.sqlite"
tar -tzf "$WIKI_BACKUP/app-data.tar.gz" > "$WIKI_BACKUP/files.txt"
sha256sum "$WIKI_BACKUP/app-data.tar.gz" "$WIKI_BACKUP/db.sqlite" > "$WIKI_BACKUP/SHA256SUMS"
(cd "$WIKI_BACKUP" && sha256sum -c SHA256SUMS)
```

每条命令都必须成功；确认归档含默认仓库的 `.git`、文档、附件以及已有新空间。
将备份复制到另一处可靠存储。保存对应配置 Secret 的受控备份和旧镜像引用，
不要将密钥内容写入 Git 或流水线日志。

### 当前使用 emptyDir

1. 完成并验证上述备份后，才可缩容：
   `kubectl -n "$WIKI_NS" scale deploy "$WIKI_DEPLOY" --replicas=0`。
2. 在同一命名空间创建持久卷声明，并确认已 `Bound`。StorageClass 必须使用集群
   实际可用的类型，不要直接套用其他环境的名称。
3. 用下面的迁移 Job 模板建立一个**恢复 Pod**：去掉 Job 外层，仅保留 Pod
   `metadata`、`spec.template.spec`，命名为 `otterwiki-restore`，将 command 改为
   `["/bin/sh", "-c", "sleep 86400"]`，保留相同 PVC、Secret 和 imagePullSecrets。
   新 PVC 必须为空；该 Pod 只用于恢复，不启动 Wiki 服务。
4. 将备份恢复到该 Pod：

```bash
kubectl -n "$WIKI_NS" cp "$WIKI_BACKUP/app-data.tar.gz" otterwiki-restore:/tmp/app-data.tar.gz
kubectl -n "$WIKI_NS" cp "$WIKI_BACKUP/db.sqlite" otterwiki-restore:/tmp/db.sqlite
kubectl -n "$WIKI_NS" exec otterwiki-restore -- /bin/sh -ec '
  test -z "$(ls -A /app-data)"
  tar -xzf /tmp/app-data.tar.gz -C /app-data
  cp /tmp/db.sqlite /app-data/db.sqlite
  rm -f /app-data/db.sqlite-wal /app-data/db.sqlite-shm
  chown -R www-data:www-data /app-data
'
```

5. 用 SQLite `PRAGMA integrity_check`、`git -C /app-data/repository fsck --full`
   验证恢复数据；已有新空间仓库也逐个执行 git fsck。确认文档数量与备份相符。
6. 更新 Deployment，把整个 `/app-data` 的 emptyDir 改为该 PVC，保持副本数为 0。
   删除恢复 Pod，待其释放卷后再运行迁移 Job。

### 当前已使用持久卷

备份验证完成后缩容到 0，等待旧 Pod 退出。迁移 Job 使用原 PVC。
若数据库、默认仓库、新空间分别挂载不同卷，Job 必须复制所有对应挂载。
不要同时运行恢复 Pod、应用 Pod 和迁移 Job，以免产生写入竞争或 RWO 挂载冲突。

## 3. 使用独立 Job 迁移

下面是标准 Kubernetes Job 示例。保存为 `otterwiki-migrate.yaml`，替换镜像、
PVC、配置 Secret 和镜像凭据 Secret 为当前环境真实值。使用已经推送到内网
Harbor 的应用镜像，不使用 runtime 基础镜像；固定不可变标签或 digest。

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: otterwiki-migrate-v5
  namespace: newcore-dev-ns
spec:
  backoffLimit: 0
  template:
    metadata:
      labels:
        app: otterwiki-migration
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: 替换为镜像仓库凭据Secret
      containers:
        - name: migrate
          image: 10.71.96.165:31104/edtp/otterwiki:替换为本次应用镜像标签
          imagePullPolicy: IfNotPresent
          command: ["/opt/venv/bin/python", "-m", "flask"]
          args: ["--app", "otterwiki.server", "db", "upgrade"]
          env:
            - name: OTTERWIKI_SETTINGS
              value: /config/settings.cfg
          volumeMounts:
            - name: data
              mountPath: /app-data
            - name: settings
              mountPath: /config
              readOnly: true
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: 替换为实际数据PVC
        - name: settings
          secret:
            secretName: 替换为实际配置Secret
            items:
              - key: settings.cfg
                path: settings.cfg
```

Job 应使用与应用匹配的卷访问身份。本项目镜像的 Web 进程以 `www-data` 运行；
恢复后确认目录可由该用户读写。若集群要求非 root，复制应用实际的 securityContext，
并确认其用户可读取配置与写入 SQLite/仓库。已有统一镜像凭据时可沿用平台配置。

```bash
kubectl -n "$WIKI_NS" apply -f otterwiki-migrate.yaml
kubectl -n "$WIKI_NS" wait --for=condition=complete job/otterwiki-migrate-v5 --timeout=300s
kubectl -n "$WIKI_NS" logs job/otterwiki-migrate-v5
```

只有 Job 成功且日志显示“数据库迁移完成”才继续。失败时保持应用停止，先查看日志。
每个版本的结构、数据和版本记录在一个事务中提交；当前版本失败会回滚，之前已经
成功的版本仍保留。排查后删除失败 Job 再重新创建；重复执行不会恢复已撤销的成员。
应用容器启动时会再次检查迁移状态，Job 已完成的版本只会显示“已应用，跳过”。
多副本或滚动发布不能依赖容器各自迁移，必须先使用独立 Job 完成升级。

## 4. 启动与验收

保持维护入口关闭，将 Deployment 更新为与迁移 Job 相同的应用镜像、配置和 PVC，
再恢复原副本数（当前部署为 1，采用 Recreate）。

```bash
kubectl -n "$WIKI_NS" scale deploy "$WIKI_DEPLOY" --replicas=1
kubectl -n "$WIKI_NS" rollout status deploy/"$WIKI_DEPLOY" --timeout=300s
```

先检查原文档、附件和历史记录，再验证管理员及普通用户的组权限、默认空间首页、
归档访问和 12/15/24px 字号。确认重建 Pod 后数据仍保留，再开放业务入口。

## 5. 回滚

保持业务入口关闭并停止应用。先额外保存升级后产生的数据（尤其是新空间），
再使用恢复 Pod 将升级前的**整套**数据库与仓库备份恢复到一个空的新 PVC，
不要在仍有文件的旧卷上直接覆盖，也不要仅删除 spaces 目录。
验证后将 Deployment 的数据卷指向恢复 PVC，恢复旧镜像与匹配配置并启动。
旧卷保留供排查；若需要保留升级后编辑内容，先单独导出，不要直接覆盖备份。

## 管理入口

空间与组在“设置 → 空间管理/组管理”维护；用户页可编辑所属组。
删除用户会同时清理组关系；新注册用户需要管理员分配组。
“内容与编辑设置”可调整全局文档字号（12–24px，默认 15px），恢复默认时回退
配置文件/环境变量中的默认值。字号不会改变导航、管理界面或编辑器输入大小。


## 6. 异步导入部署约定（v4）

应用保持 **1 个副本、1 个 uWSGI 工作进程、4 个请求线程**。内置后台线程在
收到提交后创建；不在预加载阶段启动，也不需要 Redis 或 Celery。Windows
继续使用原启动方式，但不要同时启动两个应用进程。开发自动重载会中断任务。

必须持久化整个 `/app-data`，包括：

- SQLite 数据库、默认仓库以及 `spaces` 下的空间仓库。
- 默认位于仓库父目录的 `.otterwiki-import-tasks/`（任务检查点和执行锁）。
- 每个目标仓库父目录中的 `.otterwiki-import-<任务ID>/`（上传和临时仓库），
  以及 `.<仓库名>.pre-import-*` 备份。不要用只挂载 `repository` 的卷布局。

`DOCUMENT_IMPORT_TASK_ROOT` 可指定检查点目录；所有进程必须指向同一持久目录。
独立迁移 Job 和应用应使用同一份 settings.cfg、相同环境覆盖项及全部数据挂载。
如 `REPOSITORY`、`SPACES_ROOT`、`DOCUMENT_IMPORT_TASK_ROOT` 由环境变量覆盖，
将这些相同值补充到迁移 Job 的 `env`，不要只传配置文件路径。

## 7. 按空间 Git 同步（v5）

“设置 → 仓库管理”可为每个空间绑定一个 HTTPS、SSH，或受信任内网中的明文 HTTP 远程仓库。
HTTPS/HTTP 使用用户名与密码（或 PAT），SSH 使用私钥；HTTP 会把用户名和密码以明文传输，
仅应在可信内网使用，需要加密时请改用 HTTPS。
远程分支必须已是 OtterWiki Markdown 格式；首次导入会替换目标空间的
本地仓库，应先备份。日常拉取仅允许快进，分支分叉时不会自动合并或覆盖。

密码、PAT 和 SSH 私钥使用 `SECRET_KEY` 派生的密钥加密入库。所有应用
实例和迁移 Job 必须使用同一个稳定的 `SECRET_KEY`；更换后需在管理页
重新录入仓库凭据。后台 Git 任务与旧版导入共用
`.otterwiki-import-tasks` 检查点目录，同样要求单 Web 进程和持久化数据卷。
可无歧义迁移的旧版 Webhook 会继续有效；管理员在新页生成 Webhook 后，
旧地址立即失效，应同步更新 Git 平台配置。

容器内置 Nginx 对 uWSGI 使用 `uwsgi_read_timeout`、`uwsgi_send_timeout`。
在 Deployment 的容器环境变量配置 `NGINX_UWSGI_TIMEOUT="600"`（默认 600，
只接受正整数秒数）。上传仍然占用 HTTP 请求：检查外层 Ingress/负载均衡的
请求体大小、上传及上游超时，并与 `NGINX_MAX_UPLOAD`、应用 ZIP 大小限制协调。
提高这个超时不会让整个后台导入占用请求，提交接受后返回 202。

导入页先展示上传字节进度，随后每 2 秒查询任务；短暂网络失败最多退避至
10 秒。离开或刷新页面不取消导入；任务结果保存在数据库，重新进入当前空间
导入页可继续查看。目标空间返回 423 维护提示，其他空间与登录仍可使用。
同一实例只接受一个活动任务；第二个不同请求返回 409。

执行器等待已进入目标空间的请求退出，最多 60 秒，超时即失败且不替换仓库。
应用内 Git 同步使用同一门禁，但操作系统外部的脚本、Git 命令或卷写入不受控制，
导入前必须自行停止。不要删除 `executor.lock` 来“解锁”：删除锁文件可能绕过
仍存活进程持有的锁。文件锁由进程退出时自动释放，心跳过期不能证明任务死亡。

### 中断与人工恢复

部署、重启、切换镜像前等待任务结束；本版不支持取消或断点续传。若发生退出：

1. 保留数据库、任务 JSON 检查点、任务目录和所有备份，停止应用及外部写入。
   先备份故障现场，确认没有执行中的进程，不要删除 PVC。
2. 启动恢复会先于 GitStorage 初始化执行。替换前中断时保留旧仓库；两次移动
   之间中断且目标缺失、备份有效时自动恢复备份；不自动重复导入。
3. 若目标已匹配检查点中的新 `commit`，进度页显示“仓库已切换，但任务未完整
   结束”。核对文档、附件、Git 提交和草稿；可能需要在维护工具清理旧草稿。
   **不要因中断状态直接再导入**，重导入会重建历史。
4. 无法识别的目标继续保持维护。检查点 JSON 记录 `target`、`backup`、`stage`、
   `old_commit`、`commit`。使用 `git -C <目录> rev-parse HEAD` 和
   `git -C <目录> fsck --full` 验证现场，选择已验证的旧备份或新临时仓库。
   应用停止时先将不明目标改名留存，再将选定仓库恢复到准确的 `target` 路径；
   不要直接覆盖、删除未知内容或手工把任务标记为成功。
5. 用相同配置执行 `/opt/venv/bin/python -m otterwiki.import_runtime`（Windows
   使用当前虚拟环境的 `python`）重新核对恢复。可在挂相同卷的恢复 Pod 中执行，
   不与应用同时运行。默认空间输出 0 表示可开放，1 表示仍维护；其他空间须查看
   对应任务 JSON 的 `maintenance` 和 `error`，不能只看这一行输出。
6. 恢复仓库可识别后启动应用，进入导入页查看中断说明。核对数据后再决定是否
   重新选择文件并输入确认文本重试。核对完成前不清理备份。

失败的结构迁移遵循前述整套备份恢复流程；v4、v5 的建表与各自版本记录
在同一事务提交，当前版本失败不会撤销先前已成功的版本。重复升级不会重新执行导入。
