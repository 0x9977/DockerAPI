# DockerAPI v1.0.0 多阶段构建,规格见 docs/deployment.md
# 基础镜像 ubuntu:24.04: 与 M0 测试机同源,docker.io + docker-compose-v2 实测可用

# 阶段1: 前端构建
FROM node:20 AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY web/ .
RUN npm run build

# 阶段2: 运行
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io docker-compose-v2 python3 python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home app \
    && mkdir -p /data && chown app:app /data
WORKDIR /app/backend
COPY backend/ ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple ./
COPY --from=web /app/web/dist ./app/static
USER app
ENV DATA_DIR=/data STACKS_DIR=/opt/stacks
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fs http://127.0.0.1:8000/api/health || exit 1
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
