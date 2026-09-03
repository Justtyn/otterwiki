# Docker 离线构建说明

完整操作步骤见 [内网逐步教程](cicd-intranet-step-by-step.md)。
当前实现面向 CentOS 7 / Docker 18.09 的执行机，使用普通 `docker build`。

## 文件职责

| 文件 | 作用 |
| --- | --- |
| prepare-offline.sh | 在联网准备机导出 amd64 完整运行镜像，准备 Python 离线依赖 |
| docker/Dockerfile.runtime | 在联网准备阶段制作 Bullseye/Python 3.11 运行基础镜像，包含 Git、Nginx、uWSGI |
| docker/prepare-wheels.py | 在准备容器中读取 pyproject，收集依赖与构建/测试工具，验证离线安装 |
| build.sh | 复制本地包、构建镜像、可选容器测试、推送、写发布记录 |
| Dockerfile | 容器内离线打包源码、安装依赖，保留 Nginx/uWSGI 启动结构 |
| docs/pipeline-intranet.sh | 流水线「自定义编译脚本」的两行模板 |

## 构建行为

基础镜像必须是已导入并验证过的完整 OtterWiki Python 3.11 运行镜像。
联网准备现在制作 `otterwiki-runtime:py311-bullseye`，内网试运行仍需实际完成。
旧的上游完整镜像已在用户的 Docker 18.09 上确认线程创建被 seccomp 拦截。
Bullseye 是兼容过渡方案，长期应更新宿主机运行环境并使用受支持的基础系统。
`BASE_IMAGE` 默认使用已推送的
`10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye`。
执行机需要先拉取该镜像，并完成默认安全配置下的线程测试。

Dockerfile 的构建阶段和运行阶段使用同一基础镜像。构建阶段仅从本地 wheel 目录安装，
当前源码生成新的应用 wheel 并强制重新安装；最终阶段复制安装结果和本仓库的服务配置、
静态文件。没有宿主机 Python 打包步骤，也没有内网 apt/yum/pip 联网下载步骤。

`build.sh` 使用 `DOCKER_BUILDKIT=0`，Dockerfile 不使用 `RUN --mount`、`COPY --chmod`
或 `HEALTHCHECK --start-interval`。构建及可选测试容器使用 `--network none`。
这解决构建语法和联网依赖问题，旧内核/容器运行时是否能运行基础镜像需要现场验证。

## 调用与产物

```bash
bash build.sh dev 1.0.0
```

默认推送，默认跳过自动化测试，与用户提供的 Spring Boot 构建习惯一致。
可用 `PUSH_IMAGE=false` 只构建，或 `SKIP_TESTS=false` 启用容器内测试。
测试失败时不推送；其他步骤失败时也不会写入成功发布记录。
已有成功记录在本次构建开始时清除，避免后续作业读取上一次的结果。

- `target/wheels/`：供 Docker COPY 的本地依赖副本。
- `target/image-name.txt`：成功构建的镜像全名。
- `target/image.env`：镜像全名及 `PUSHED=true/false` 状态。

完整运行镜像已有 Python、Git、Nginx、uWSGI 等系统组件。
配置 Docker 凭据时使用实际执行流水线的账号，并与 `DOCKER_USE_SUDO` 的选择保持一致。
镜像标签中的环境名不自动转换为应用配置；持久化数据与运行配置在部署平台设置。
