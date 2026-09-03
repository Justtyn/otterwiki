#!/usr/bin/env bash
set +x
set -euo pipefail

# 首次配置只改这里；流水线仍然只调用 bash build.sh 环境 版本。
WHEELHOUSE="${WHEELHOUSE:-/home/app/apstack/python-wheels-bullseye}"
# 默认使用已推送到内网的运行镜像；Docker 18.09 使用 py311-bullseye 版本。
# 使用完整镜像保留项目现有的 Nginx/uWSGI/Git，不用每次构建安装系统软件。
BASE_IMAGE="${BASE_IMAGE-10.71.96.165:31104/edtp/otterwiki-runtime:py311-bullseye}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-10.71.96.165:31104/edtp/otterwiki}"
SKIP_TESTS="${SKIP_TESTS:-true}"
PUSH_IMAGE="${PUSH_IMAGE:-true}"
DOCKER_USE_SUDO="${DOCKER_USE_SUDO:-false}"

fail() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

[[ $# -eq 2 ]] || fail '用法：bash build.sh <环境> <版本号>，例如 bash build.sh dev 1.0.0'
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
mkdir -p target
rm -f target/image-name.txt target/image.env

ENV_ID="$1"
VERSION_NUM="$2"
[[ -n "$ENV_ID" && -n "$VERSION_NUM" ]] || fail '环境和版本号不能为空。'
IMAGE_TAG="${IMAGE_TAG:-${ENV_ID}-${VERSION_NUM}-$(date +%Y%m%d)}"
[[ "$IMAGE_TAG" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || fail '镜像标签无效。'
IMAGE="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
for FLAG in "$SKIP_TESTS" "$PUSH_IMAGE" "$DOCKER_USE_SUDO"; do
    [[ "$FLAG" == true || "$FLAG" == false ]] || \
        fail 'SKIP_TESTS、PUSH_IMAGE、DOCKER_USE_SUDO 只能为 true 或 false。'
done

[[ -d "$WHEELHOUSE" ]] || fail '离线包目录不存在，请在 build.sh 顶部配置 WHEELHOUSE。'
WHEELHOUSE="$(cd -- "$WHEELHOUSE" && pwd -P)"
case "$WHEELHOUSE" in
    "$PROJECT_DIR/target"|"$PROJECT_DIR/target/"*)
        fail '离线包原始目录不能放在会被清理的 target 目录内。' ;;
esac
[[ -n "$BASE_IMAGE" ]] || fail '请在 build.sh 顶部填写 BASE_IMAGE，即内网完整 OtterWiki 运行镜像地址。'

docker_cmd=(docker)
if [[ "$DOCKER_USE_SUDO" == true ]]; then
    docker_cmd=(sudo -n docker)
fi

echo '检查构建环境'
"${docker_cmd[@]}" info >/dev/null
"${docker_cmd[@]}" image inspect "$BASE_IMAGE" >/dev/null || \
    fail '本机未找到基础镜像，请先 docker load 离线导入，或从内网仓库 docker pull。'

# Docker 只能 COPY 构建目录里的文件；此处只复制文件，不执行宿主机 Python。
rm -rf target/wheels
mkdir -p target/wheels
shopt -s nullglob
for WHEEL in "$WHEELHOUSE"/*.whl; do
    [[ "${WHEEL##*/}" == otterwiki-*.whl ]] && continue
    cp "$WHEEL" target/wheels/
done
wheels=(target/wheels/*.whl)
[[ ${#wheels[@]} -gt 0 ]] || fail '离线包目录中没有依赖 wheel，请先执行联网准备步骤。'

echo "构建镜像：$IMAGE"
DOCKER_BUILDKIT=0 "${docker_cmd[@]}" build --network none --pull=false \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    --build-arg "GIT_TAG=$IMAGE_TAG" \
    -t "$IMAGE" .

if [[ "$SKIP_TESTS" == false ]]; then
    # 测试在刚生成的镜像内执行，使用同一套运行依赖；临时容器退出后删除。
    "${docker_cmd[@]}" run --rm --network none --entrypoint /bin/sh \
        -v "$PROJECT_DIR:/src:ro" "$IMAGE" -ec '
        export PIP_CONFIG_FILE=/dev/null PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
        /opt/venv/bin/python -m pip install --no-index --find-links=/src/target/wheels "otterwiki[dev]"
        cd /src
        OTTERWIKI_SETTINGS="" /opt/venv/bin/python -m pytest tests docs/plugin_examples/tests
    '
fi

if [[ "$PUSH_IMAGE" == true ]]; then
    echo "推送镜像：$IMAGE"
    "${docker_cmd[@]}" push "$IMAGE"
fi

printf '%s\n' "$IMAGE" > target/image-name.txt
printf 'IMAGE=%s\nPUSHED=%s\n' "$IMAGE" "$PUSH_IMAGE" > target/image.env
echo "构建完成：$IMAGE"
