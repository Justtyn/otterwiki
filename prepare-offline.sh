#!/usr/bin/env bash
# 仅在可联网、能运行 Linux amd64 容器的准备机上执行。
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo '用法：bash prepare-offline.sh；Python 已包含在运行镜像内，无需额外安装包。' >&2
    exit 1
fi
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
# 在联网容器内补齐服务组件，生成适配旧 Docker 的完整运行镜像。
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.11-slim-bullseye}"
RUNTIME_IMAGE="${RUNTIME_IMAGE:-otterwiki-runtime:py311-bullseye}"
FINAL_DIR="$PROJECT_DIR/offline-bundle-bullseye"
ARCHIVE="$PROJECT_DIR/otterwiki-offline-bullseye.tar.gz"
# 清理前一次遗留的空目录；rmdir 不会删除任何非空目录。
if [[ -d "$FINAL_DIR" && ! -e "$ARCHIVE" ]]; then
    rmdir "$FINAL_DIR/python-wheels" "$FINAL_DIR" 2>/dev/null || true
fi
if [[ -e "$FINAL_DIR" || -e "$ARCHIVE" ]]; then
    echo 'Bullseye 离线材料已存在，请先移走旧输出再执行。' >&2
    exit 1
fi
# 成功前只写本次临时目录；失败自动清理，重跑不会被半成品挡住。
WORK_DIR="$(mktemp -d "$PROJECT_DIR/.offline-prep.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
BUNDLE_DIR="$WORK_DIR/offline-bundle"
mkdir -p "$BUNDLE_DIR/python-wheels"

echo '拉取 Python 3.11 Bullseye 基础镜像'
for ATTEMPT in 1 2 3; do
    if docker pull --platform linux/amd64 "$PYTHON_IMAGE"; then
        break
    fi
    if [[ "$ATTEMPT" == 3 ]]; then
        echo '镜像拉取连续失败，请检查 Docker Desktop 的代理连接后重试。' >&2
        exit 1
    fi
    echo "镜像拉取失败，3 秒后重试（$ATTEMPT/3）。" >&2
    sleep 3
done
echo '在联网容器中制作完整运行镜像'
docker build --platform linux/amd64 --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" \
    -f docker/Dockerfile.runtime -t "$RUNTIME_IMAGE" .
docker run --rm --platform linux/amd64 --entrypoint /bin/sh "$RUNTIME_IMAGE" -ec '
    /opt/venv/bin/python -c "import sys, threading; assert sys.version_info[:2] == (3, 11); t=threading.Thread(); t.start(); t.join()"
    test -x /entrypoint.sh
    test -x /usr/bin/supervisord
    test -x /usr/bin/uwsgi_python3
    test -f /app/uwsgi.ini
    git --version
'

echo '下载项目需要的全部 Python 包'
docker run --rm --platform linux/amd64 --entrypoint /bin/sh \
    -v "$PROJECT_DIR/pyproject.toml:/src/pyproject.toml:ro" \
    -v "$PROJECT_DIR/docker/prepare-wheels.py:/src/prepare-wheels.py:ro" \
    -v "$BUNDLE_DIR/python-wheels:/wheelhouse" \
    "$RUNTIME_IMAGE" -ec '
        /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel
        /opt/venv/bin/python /src/prepare-wheels.py
    '

echo '导出运行镜像和离线安装包'
docker save -o "$BUNDLE_DIR/otterwiki-runtime.tar" "$RUNTIME_IMAGE"
printf '%s\n' "$RUNTIME_IMAGE" > "$BUNDLE_DIR/runtime-image.txt"
cp pyproject.toml "$BUNDLE_DIR/pyproject.toml"
tar -czf "$WORK_DIR/otterwiki-offline.tar.gz" -C "$WORK_DIR" offline-bundle
mv "$BUNDLE_DIR" "$FINAL_DIR"
mv "$WORK_DIR/otterwiki-offline.tar.gz" "$ARCHIVE"
echo '准备完成：将 otterwiki-offline-bullseye.tar.gz 和更新后的完整项目源码导入内网。'
