# 基础镜像使用已导入内网的完整 OtterWiki 运行镜像（Python 3.11）。
# 由 build.sh 传入；不默认访问公网，不在这里安装 Debian 或其他系统包。
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build-stage
USER root
ENV PIP_CONFIG_FILE=/dev/null \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 保留原项目 Nginx/uWSGI 的启动结构，提前检查基础镜像是否合适。
RUN /opt/venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 11)' && \
    git --version && \
    test -x /entrypoint.sh && test -x /usr/bin/supervisord && \
    test -x /usr/bin/uwsgi_python3 && \
    test -f /app/uwsgi.ini

# 所有 Python 打包和依赖安装均在容器内完成。
COPY target/wheels /tmp/otterwiki-wheels
RUN /opt/venv/bin/python -m pip install --no-cache-dir --no-index \
    --find-links=/tmp/otterwiki-wheels --upgrade pip setuptools wheel
WORKDIR /src
COPY pyproject.toml MANIFEST.in README.md LICENSE /src/
COPY otterwiki /src/otterwiki
COPY scripts /src/scripts
RUN /opt/venv/bin/python -m pip wheel --no-index \
    --find-links=/tmp/otterwiki-wheels --no-deps --no-build-isolation \
    --wheel-dir /tmp/app-wheel . && \
    /opt/venv/bin/python -m pip install --no-cache-dir --no-index \
    --find-links=/tmp/otterwiki-wheels --force-reinstall \
    /tmp/app-wheel/otterwiki-*.whl && \
    /opt/venv/bin/python -m pip check && \
    cd /tmp && \
    /opt/venv/bin/python -c 'from PIL import _imaging; from lxml import etree; import regex, yaml, sqlalchemy; from scripts.migrate_apstack_docs import Migration'

# 使用相同基础镜像，保证 Python 和系统库一致；最终镜像不带离线包目录。
FROM ${BASE_IMAGE} AS production-stage
USER root
ARG GIT_TAG
ENV GIT_TAG=$GIT_TAG
WORKDIR /app
# 先移除基础镜像的旧文件，避免旧迁移脚本优先于本次 wheel 中的版本被加载。
RUN rm -rf /opt/venv /app/otterwiki/static /app/scripts
COPY --from=build-stage /opt/venv /opt/venv
# Nginx 直接提供静态文件，使用本次源码中的版本。
COPY otterwiki/static /app/otterwiki/static
# 沿用本仓库的入口与服务配置，兼容旧 Docker 的 COPY 语法。
COPY docker/uwsgi.ini /app/uwsgi.ini
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/stop-supervisor.sh /etc/supervisor/stop-supervisor.sh
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh /etc/supervisor/stop-supervisor.sh
# 在最终运行目录校验实际会加载本次 wheel 中的迁移脚本，而不是基础镜像残留。
RUN /opt/venv/bin/python -c 'from inspect import signature; from scripts.migrate_apstack_docs import Migration; assert "progress" in signature(Migration).parameters'
EXPOSE 80 8080
HEALTHCHECK --interval=30s --timeout=3s --retries=3 --start-period=30s \
    CMD curl -A "docker-healthcheck" -f http://localhost:8080/-/healthz || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
