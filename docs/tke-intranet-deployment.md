# TKE 内网配置导入

本教程新建 `otterwiki` 应用。命名空间暂用参考截图标签中的 `newcore-dev-ns`，
平台项目标签暂用 `newcore-dev`；导入前与实际页面选择核对。
不会覆盖参考 Java 应用 `aps-idp-ui`。

## 1. 准备导入文件

复制 `deploy/tke/otterwiki.yaml.example` 为 `deploy/tke/otterwiki.local.yaml`。
本地副本已被 Git 忽略，真实密钥只保存在内网配置管理中。
主文件包含 Secret、PVC、Deployment 和 Service 四个资源，以 `---` 分隔。

| 修改位置 | 填写内容 |
| --- | --- |
| 所有 namespace 及平台标签 | 实际命名空间和项目；示例 newcore-dev-ns / newcore-dev |
| Secret 中 SECRET_KEY | 随机密钥；已有正式部署保持原值 |
| Secret 中 APSTACK_API_BASE_URL | 容器能访问的内网接口地址或 Kubernetes Service 地址 |
| PVC 中 storageClassName | 集群中实际存在的存储类名称 |
| Deployment 中 image | 构建成功后 target/image-name.txt 的业务镜像全名 |
| Deployment 中 imagePullSecrets | 同一命名空间中可拉取 Harbor 镜像的凭据名称 |

`SECRET_KEY = 'CHANGE ME'` 是故意保留的无效占位值，必须替换后部署。
在有基础镜像的内网机器生成一次密钥，不需要宿主机 Python：

```bash
docker run --rm --network none --entrypoint /opt/venv/bin/python \
  10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye \
  -c 'import secrets; print(secrets.token_hex(32))'
```

将输出写入 Secret 的配置文本。`settings.cfg` 使用 Python 语法，放在
`stringData.settings.cfg: |` 下并保留缩进；还可以迁入自己已有的其他配置。

## 2. 准备集群存储与仓库凭据

在平台中查看存储类列表；如有已配置集群访问权限的 kubectl，可只读查询：

```bash
kubectl get storageclass
kubectl -n newcore-dev-ns get pvc
kubectl -n newcore-dev-ns get secret
```

PVC 示例请求 10Gi、ReadWriteOnce；选择适合 Git/SQLite 的文件系统卷，优先块存储。
没有存储类时，需要先通过平台供应一个可用的持久卷/PVC，不能随便填写存储类名称。
如果已经准备了存放 OtterWiki 数据的 PVC，删除模板中的 PVC 创建段，
把 Deployment 的 `claimName` 改成已有 PVC 名称，保留原有数据。
采用延迟绑定的存储类时，PVC 在创建 Deployment 后才进入 Bound 属于正常情况。

在同一命名空间通过平台的镜像仓库凭据功能，创建 `harbor-pull-secret`：
仓库地址为 `10.71.96.165:31104`，填写有 `edtp/otterwiki` 拉取权限的账号。
也可以复用已有凭据，改模板中的名称。若节点或 ServiceAccount 已提供有效凭据，
移除模板中的 `imagePullSecrets` 即可。

编译机的 `docker login` 不会给 Kubernetes 节点配置凭据。
节点还需要能访问 Harbor；HTTP 仓库或内部证书信任由节点容器运行时管理，
不能通过 Deployment 的环境变量设置。

## 3. 导入资源

在目标命名空间导入填写完成的主文件。若应用页面只接受 Deployment，
在平台对应的 Secret、存储声明、Service 页面分别创建其他资源，然后导入 Deployment。
先准备 Secret 和 PVC 声明，再创建 Deployment 与 Service。

截图的 `app.k8s.io/v1beta1 Application` 是平台的应用分组资源。
若该导入入口需要这一层，可一并导入 `deploy/tke/application.yaml`，或将它的内容
放在主文件前面，用 `---` 分隔。名称和平台分组标签需要与主文件一致。
若集群提示不识别 `Application` 类型，则只导入主文件。

本次配置对应关系：

```text
Secret otterwiki-settings 中的 settings.cfg
  → 挂载为 /config/settings.cfg
  → OTTERWIKI_SETTINGS=/config/settings.cfg

PVC otterwiki-data
  → 挂载到 /app-data
  → 保存 repository/ 文档仓库和 db.sqlite 数据库

内网访问入口 → Service otterwiki:80 → Pod:8080
```

配置文件挂载在 `/config`，与可写数据目录分开。入口脚本会对 `/app-data`
执行递归 chown，因此不要将只读 Secret 文件挂到这个数据目录中。
保留镜像自带的启动命令、可写根文件系统与初始化权限；当前镜像先由 root 初始化，
uWSGI 再以 www-data 运行。无需填写 Java 的 BEFORE_ARGS，也无需宿主机配置路径。

模板使用单副本和 Recreate，沿用项目 Helm 配置对 Git 内容仓库的部署约束。
资源申请是起始示例，按可用容量和实际导入文档的内存需求调整。
节点选择仅限制 amd64 架构，未固定为截图中的具体节点，便于按持久卷位置调度。

## 4. 检查启动与配置

在平台查看 Pod 状态、事件和容器日志。也可在已连接集群的机器执行：

```bash
kubectl -n newcore-dev-ns get deployment,pod,svc,pvc
kubectl -n newcore-dev-ns rollout status deployment/otterwiki
kubectl -n newcore-dev-ns logs deployment/otterwiki --tail=100
```

在平台进入应用容器终端，检查配置文件可读与健康接口，不输出密钥：

```bash
test -r /config/settings.cfg && echo '配置文件可读'
curl -f http://localhost:8080/-/healthz
```

仅检查文件可读不会验证业务参数；健康接口正常后，再检查页面及 APStack 接口是否可用。

| 现象 | 检查位置 |
| --- | --- |
| ImagePullBackOff | 业务镜像是否已推送、凭据和命名空间、节点到 Harbor 的网络与信任配置 |
| Secret not found / FailedMount | Secret 名称、settings.cfg 键名、挂载、命名空间 |
| PVC Pending / Pod Pending | 存储供应、容量、访问模式、节点架构和存储拓扑，查看事件 |
| chown: Operation not permitted | 数据存储是否允许入口脚本修改属主，例如 NFS root_squash 限制 |
| SECRET_KEY 配置错误 | 是否仍是 CHANGE ME 占位值 |
| 没有页面但 Pod 正常 | Service 选择器、端口和内网访问入口 |

## 5. 配置内网访问入口

Service 使用 ClusterIP，只在集群内提供服务。通过平台已有的内网路由/Ingress
功能创建访问入口，后端选 Service `otterwiki`，端口 `80`，路径 `/`。
域名使用实际可用的内网域名，并按平台要求配置解析与证书；不能从截图确定具体值。

若先临时验证页面，可在连接集群的本机运行：

```bash
kubectl -n newcore-dev-ns port-forward service/otterwiki 18080:80
```

在运行该命令的同一台机器访问 `http://127.0.0.1:18080`。
此转发仅用于临时检查，命令退出后访问入口关闭。

## 6. 后续更新

改业务代码：流水线发布新的业务镜像，更新 Deployment 中的 image。
改运行配置：更新 Secret 中的 settings.cfg，等待配置更新后在平台重建 Pod；
应用在启动时读取配置，因此仅更新 Secret 不会让当前进程重新读取配置。
也可执行 `kubectl -n newcore-dev-ns rollout restart deployment/otterwiki`。
两种更新均保留原 PVC 和 SECRET_KEY，无需重新生成基础镜像或离线依赖。

参考：[Secret 文件挂载](https://kubernetes.io/docs/concepts/configuration/secret/)、
[持久卷与 PVC](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)、
[私有仓库凭据](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/)、
[Application CRD](https://github.com/kubernetes-sigs/application)。
