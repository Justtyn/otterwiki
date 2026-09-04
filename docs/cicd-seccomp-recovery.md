# 线程测试确认 seccomp 问题后的替换步骤

原镜像默认运行时创建线程失败，临时关闭 seccomp 后通过，说明旧 Docker 的系统调用过滤
与原镜像运行库不兼容。现在改用 `otterwiki-runtime:py311-bullseye`。
正常构建和部署仍保留 seccomp，不添加 `unconfined` 参数。

## 1. 导入新的离线包

把新生成的 `otterwiki-offline-bullseye.tar.gz` 传到内网。
以下命令在存放这个文件的目录执行；`bullseye-import` 应为新的空目录：

```bash
mkdir bullseye-import
tar -xzf otterwiki-offline-bullseye.tar.gz -C bullseye-import
docker load -i bullseye-import/offline-bundle/otterwiki-runtime.tar
```

旧的 `otterwiki-offline.tar.gz` 是之前的材料，不是本次替换包。

## 2. 在默认过滤规则下确认线程正常

```bash
docker run --rm --network none \
  --entrypoint /opt/venv/bin/python \
  otterwiki-runtime:py311-bullseye \
  -c "import threading; t=threading.Thread(target=lambda: print('线程测试通过')); t.start(); t.join()"
```

这次没有 `--security-opt seccomp=unconfined`。看到「线程测试通过」后继续。
若仍失败，保留报错并暂停后续构建，需要检查该机器的具体运行时限制。

## 3. 复制离线依赖并保存内网基础镜像

```bash
mkdir -p /home/app/apstack/python-wheels-bullseye
cp bullseye-import/offline-bundle/python-wheels/* /home/app/apstack/python-wheels-bullseye/
docker tag otterwiki-runtime:py311-bullseye \
  10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye
docker login 10.71.96.165:31104
docker push 10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye
```

Docker 登录和构建使用相同账号；需要 sudo 时也保持一致。
当前 `build.sh` 每次先从内网仓库拉取基础镜像，因此需要先完成上述入库和登录步骤；
拉取失败时构建停止，不回退到本地旧缓存。

## 4. 修改 build.sh 的两项配置

使用本次更新后的代码，在项目的 `build.sh` 顶部填写：

```bash
WHEELHOUSE="${WHEELHOUSE:-/home/app/apstack/python-wheels-bullseye}"
BASE_IMAGE="${BASE_IMAGE:-10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye}"
```

在项目根目录先验证构建：

```bash
PUSH_IMAGE=false bash build.sh dev 1.0.0
```

然后按完整教程检查应用启动、再执行正式推送。流水线页面仍是调用 `build.sh` 的两行。

## 兼容范围

本地已显式模拟 `clone3` 返回 EPERM：原镜像重现线程失败，新镜像线程测试通过，
并完成断网业务构建和相同拦截条件下的应用健康检查。
这只验证了对应的系统调用兼容性，不能替代内网 Docker 18.09 / 内核 3.10 的现场测试。
本方案是兼容过渡：Debian 11 的 LTS 已于 2026-08-31 结束，长期应更新宿主机运行环境，
再切换到受支持的基础系统。参见 [Debian 生命周期](https://www.debian.org/releases/bullseye/)
和 [Docker 上游兼容问题说明](https://github.com/docker-library/official-images/issues/16829)。
