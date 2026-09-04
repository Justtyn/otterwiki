# OtterWiki：内网 Docker 构建与离线依赖准备教程

适用环境：截图中的 CentOS 7、x86_64、Docker 18.09.6、Bash 4.2。
Python 打包、依赖安装、可选测试都在容器内执行。流水线机器使用已有的 Docker，
流水线页面只负责调用 `build.sh`。
每次构建先从内网 Harbor 拉取基础镜像，再在禁用网络的容器中打包和安装依赖，
最后推送业务镜像到内网 Harbor；执行机无需访问公网。

## 1. 先分清需要准备的东西

| 内容 | 怎样进入内网 | 用途 |
| --- | --- | --- |
| 完整运行镜像 | 联网制作，docker save 导出，内网 docker load 导入 | 已带 Python 3.11、Git、Nginx、uWSGI 和系统库 |
| Python 离线包目录 | 联网容器自动下载所有依赖，传入内网 | 安装业务依赖、构建工具和测试工具 |
| 本项目源码 | 内网 Git 仓库或文件传输 | 构建你定制过的业务程序 |

旧的 `redimp/otterwiki:2.23.0` 已在截图中的 Docker 18.09 上确认存在 seccomp 线程兼容问题。
当前准备脚本使用 `python:3.11-slim-bullseye`，在联网容器内装好 Nginx、Git、Supervisor，
并用同一个 Python 3.11 编译 uWSGI，得到 `otterwiki-runtime:py311-bullseye`。
内网只导入这个完整运行基础镜像，再用当前仓库源码构建业务镜像。

这是兼容旧 Docker 的过渡方案。[Debian 11 的 LTS 已于 2026-08-31 结束](https://www.debian.org/releases/bullseye/)，
长期使用应更新宿主机容器运行环境，再切换到受支持的基础系统。
已导入旧材料的用户可直接按 [seccomp 问题后的替换步骤](cicd-seccomp-recovery.md) 操作。

这是一个有现成 Nginx/uWSGI 启动配置的 Wiki 项目，不能直接套用通用示例中的
`CMD ["python", "app.py"]`。也不要把这里的基础镜像替换成 JDK 镜像或普通 Python slim 镜像。

**Debian 是容器内的 Linux 发行版。** 它的运行文件已随完整镜像一起导入，
不会把编译机上的 CentOS 换成 Debian，也不用另外下载 Debian 安装盘。
Python 也已在镜像中，无需在编译机上安装解释器或执行 `yum install`。

下面的准备工作由你操作即可。联网准备只需在依赖或基础镜像变化时重做。

## 2. 在能联网的电脑准备离线材料

准备一台能运行 Linux 容器的 Docker 电脑。Windows 可使用 Docker Desktop 的 Linux
容器模式，在 WSL2 的 Bash 终端运行以下命令；不要在 Windows Python 中直接下载依赖。

先把**这次更新后的完整项目源码**放到联网电脑，在项目根目录执行：

```bash
bash prepare-offline.sh
```

脚本自动完成：

1. 拉取官方 Python 镜像的 `linux/amd64` 版本，通过 `docker/Dockerfile.runtime` 制作完整运行基础镜像。
2. 在这个镜像的容器中读取 `pyproject.toml`，下载项目全部依赖和间接依赖。
3. 把 `pip`、`setuptools`、`wheel` 和项目的测试工具一并准备好。
4. 对只有源码包的依赖生成 wheel，并在干净虚拟环境中验证本地安装和依赖一致性。
5. 导出基础镜像并压缩所有材料。

成功后，项目根目录得到 `otterwiki-offline-bullseye.tar.gz`，解压内容如下：

```text
offline-bundle/
├── otterwiki-runtime.tar
├── runtime-image.txt
├── pyproject.toml
└── python-wheels/
    ├── requirements-offline.txt
    └── 各个依赖的 .whl 文件
```

`python-wheels` 已包含完整依赖集合，不需要你逐个查找 Flask、Pillow 等包。
在目标镜像中下载是为了使这些包与容器的 Python、CPU 架构和系统库匹配。
wheel 是 Python 的可安装包格式，参见 [pip wheel 文档](https://pip.pypa.io/en/stable/cli/pip_wheel/)。

如果提示指定版本不存在、下载失败或缺少编译工具，这一步还没有完成：
根据具体报错补齐联网准备环境，直到脚本成功。不要把部分下载目录直接用于流水线，
也不要为了让下载通过而随意修改项目锁定的依赖版本。

下载失败可以直接重跑：镜像拉取会最多尝试 3 次，未完成的临时材料会自动清理。
空的 `offline-bundle-bullseye/python-wheels` 也会自动清理。
如果之前已成功生成 Bullseye 材料，再次准备前先把旧的 `offline-bundle-bullseye` 和
`otterwiki-offline-bullseye.tar.gz` 移到备份目录；脚本不会覆盖已有成果。

macOS 上若拉取镜像出现 `EOF`，先检查 Docker Desktop 的代理和重试结果。
终端提示「代理已开启」不代表 Docker 一定采用相同代理。Docker Desktop 可以跟随系统代理，
也可以在 Settings → Resources → Proxies 手动配置。
本机 HTTP 代理若监听 7897，则 HTTP 和 HTTPS 两项代理地址均可填写
`http://127.0.0.1:7897`；HTTPS 目标地址不要求代理本身使用 HTTPS 协议。
代理模式和当前系统设置以实际配置为准，见 [Docker 代理说明](https://docs.docker.com/desktop/settings-and-maintenance/settings/#proxies)。

## 3. 把离线材料导入内网编译机

把 `otterwiki-offline-bullseye.tar.gz` 传到编译机上的一个目录，例如 `/home/app/install`。
在这个目录中执行：

```bash
tar -xzf otterwiki-offline-bullseye.tar.gz
mkdir -p /home/app/apstack/python-wheels-bullseye
cp offline-bundle/python-wheels/* /home/app/apstack/python-wheels-bullseye/
docker load -i offline-bundle/otterwiki-runtime.tar
```

已有旧材料时请在新的空目录中解压。新的 Python 包目录与旧包目录分开存放。
这一步只复制 Python 包文件并导入 Docker 镜像，不在宿主机安装 Python 包。
`docker load` 会恢复镜像及其标签，见 [Docker 导入说明](https://docs.docker.com/reference/cli/docker/image/load/)。

**在截图中的旧 Docker 上，先验证容器确实能运行：**

```bash
docker run --rm --network none --entrypoint /bin/sh otterwiki-runtime:py311-bullseye -ec '
  /opt/venv/bin/python -c "import sys, ssl, threading; print(sys.version); t=threading.Thread(); t.start(); t.join()"
  git --version
  nginx -v
  /usr/bin/uwsgi_python3 --version
'
```

应打印 Python 3.11 和各组件版本，最后正常退出。
普通 Dockerfile 语法兼容 Docker 18.09，并不保证任意新镜像都兼容旧内核和旧容器运行时。
如果这里发生 `Operation not permitted`、线程创建失败、镜像格式或动态库错误，
先记录完整错误并处理实际兼容问题；此时继续修改流水线脚本不能解决问题。

## 4. 把基础镜像存到内网仓库

沿用你已有的仓库地址和 `edtp` 项目：

```bash
docker tag otterwiki-runtime:py311-bullseye \
  10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye
docker login 10.71.96.165:31104
docker push 10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye
```

这个内网镜像名称是由上面的命令创建的，并非假定你仓库里原本就有。
你的截图已经把该仓库配置为 Insecure Registry，无需重复修改 Docker 配置。

登录时使用**真正执行流水线的 Linux 账号**。截图中 root 登录成功，不代表流水线账号已登录。
如果流水线必须用 `sudo -n docker`，则登录、导入和构建都使用一致的 sudo 方式。
密码在 `docker login` 提示时输入，不写入 Git 或编译脚本。

以后增加另一台执行机，需要复制离线包目录，并配置仓库登录凭据。
`build.sh` 会自动拉取基础镜像；也可以先手动执行下面的命令验证连接和权限：

```bash
docker pull 10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye
```

这里仅访问内网仓库。构建脚本每次都先拉取基础镜像，再检查本地镜像是否可用。
即使本地已有镜像，也会先与仓库同步；拉取失败则停止，不继续使用旧缓存。

## 5. 修改项目里的 build.sh

打开项目根目录的 `build.sh`，顶部配置如下。`BASE_IMAGE` 已默认指向你推送成功的
内网地址，只需确认离线包目录和 Docker 执行方式与流水线账号一致：

```bash
WHEELHOUSE="${WHEELHOUSE:-/home/app/apstack/python-wheels-bullseye}"
BASE_IMAGE="${BASE_IMAGE-10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-10.71.96.165:31104/edtp/otterwiki}"
SKIP_TESTS="${SKIP_TESTS:-true}"
PUSH_IMAGE="${PUSH_IMAGE:-true}"
DOCKER_USE_SUDO="${DOCKER_USE_SUDO:-false}"
```

- `WHEELHOUSE`：刚才存放离线包的目录，流水线账号需要能读取。
- `BASE_IMAGE`：包含 Python 和系统组件的基础镜像。
- `IMAGE_REPOSITORY`：你最终发布的业务镜像地址，可按项目命名修改。
- `SKIP_TESTS`：与参考 Spring Boot 脚本一样默认跳过测试，设为 `false` 时在容器中测试。
- `PUSH_IMAGE`：默认构建完成后推送，设为 `false` 可先在本机验证。
- `DOCKER_USE_SUDO`：根据流水线账号的实际执行方式选择 `true` 或 `false`。

文件保存为 LF 换行；仓库的 `.gitattributes` 已为 Shell 脚本设置 LF。
`Dockerfile` 已配置好离线安装，只需随源码提交，不必在页面复制它的内容。

## 6. 先在内网机器手动构建一次

使用流水线账号进入**项目源码根目录**，先执行：

```bash
PUSH_IMAGE=false bash build.sh dev 1.0.0
```

实际过程是：

```text
build.sh 从内网 Harbor 拉取基础镜像
  → 检查本地基础镜像
  → 复制离线包到构建目录
  → docker build
  → 容器内安装本地构建工具
  → 容器内把本次源码生成 wheel
  → 容器内安装业务程序和全部运行依赖
  → 生成定制镜像
```

Docker 的构建步骤使用 `--network none`，pip 使用 `--no-index --find-links`。
因此依赖缺失会直接报错，不会去访问公网补包。最终镜像不包含准备时的整个离线包目录。
`docker build --pull=false` 复用前面显式拉取的基础镜像，不代表整个脚本不拉取镜像。
即使设置 `PUSH_IMAGE=false`，构建前仍会拉取基础镜像，只是不推送最终业务镜像。

成功后用独立的测试数据卷启动，避免接触已有 Wiki 数据：

```bash
IMAGE="$(cat target/image-name.txt)"
docker run -d --name otterwiki-ci-check \
  -p 18080:8080 -v otterwiki-ci-check-data:/app-data "$IMAGE"
docker logs otterwiki-ci-check
```

等待服务启动后，浏览器访问 `http://编译机IP:18080`。
如果宿主机没有 curl，可以用容器自带的 curl 检查：

```bash
docker exec otterwiki-ci-check curl -f http://localhost:8080/-/healthz
```

检查结束执行 `docker rm -f otterwiki-ci-check`，测试数据卷会保留。
若这个容器名已被占用，换一个名字，不要删除不确定用途的容器。

需要运行自动化测试时：

```bash
SKIP_TESTS=false PUSH_IMAGE=false bash build.sh dev 1.0.0
```

确认后正式构建并推送：

```bash
bash build.sh dev 1.0.0
```

成功推送后，`target/image-name.txt` 写入镜像全名。
默认标签为 `环境-版本-日期`；同一天反复使用相同环境和版本会覆盖同名标签，
需要保留每次产物时请使用不同版本号，或通过 `IMAGE_TAG` 指定唯一标签。

## 7. 填写流水线页面

编译类型选择「自定义」，编译脚本只填：

```bash
cd otterwiki || exit 1
bash build.sh "${env}" "${version}"
```

把 `otterwiki` 改成流水线实际检出的目录名。如果脚本起始目录已经是项目根目录，
只保留第二行。流水线提供 `env` 和 `version` 两个参数，例如 `dev`、`1.0.0`。

| 页面项目 | 填写方式 |
| --- | --- |
| 编译类型 | 自定义 |
| 编译脚本 | 上面的两行 |
| 关联执行机 | 刚才完成镜像导入、包复制和登录验证的那台机器 |
| 归档制品 | 按工作区路径填写 otterwiki/target；若项目直接检出到工作区则填 target |
| 获取制品 | 本次流程无需从 Maven 构建作业获取制品 |

`target` 中有离线依赖副本和发布记录，镜像本身在 Docker/镜像仓库中，
不会自动变成这个目录下的 tar 文件。如果平台支持只归档文件，可只归档
`target/image-name.txt` 和 `target/image.env`；无需归档离线依赖副本。

删除旧的 `java -version`、Maven 调用和 `aps-idp/*/target` 归档路径。
构建参数都维护在 Git 中的 `build.sh`，页面不用填写一长串配置。

## 8. 以后代码和依赖更新怎么办

只改 Python 业务代码、模板或已经打包的静态资源：直接运行流水线。
修改 `pyproject.toml` 依赖或更换基础镜像：用更新后的源码重新执行第 2 步，
导入新的离线材料；建议使用新的离线包目录，修改 `WHEELHOUSE`，避免混入旧版本包。

若修改 `frontend/src`，需要另行准备 Node/npm 依赖并生成前端产物；
当前流程使用仓库已提交的 `otterwiki/static/js/cm6-bundle.min.js`。

部署平台使用最终业务镜像，容器端口为 8080，持久化目录为 `/app-data`。
`env` 参数仅参与镜像标签，不会自动配置业务环境；APStack 接口地址等仍需在部署时配置。

TKE 的 YAML 导入、settings.cfg 挂载和持久卷配置见
[TKE 内网部署教程](tke-intranet-deployment.md)。
