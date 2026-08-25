# 部署与安全加固

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25
- **目标平台: Linux 宿主机(生产)**;Windows Docker Desktop 仅作为开发机参考,差异见对应章节;测试环境为 VMware 上的 Ubuntu/Debian 虚拟机(见 roadmap M0)

## 镜像构建(多阶段)

> 基础镜像用 **ubuntu:24.04**(与 M0 测试机同源):Debian slim 源里没有 `docker-compose-plugin` 包名,而 Ubuntu 官方源的 `docker.io + docker-compose-v2` 在 M0 已实测可用,规避 F13 包名坑。构建须在 M6 早期做一次冒烟验证 `docker compose version` 在容器内可用。

```dockerfile
# 阶段1: 构建前端
FROM node:20 AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY web/ .
RUN npm run build

# 阶段2: 运行(ubuntu:24.04 自带 python3.12)
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io docker-compose-v2 python3 python3-pip python3-venv curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --create-home app && mkdir -p /data && chown app:app /data
WORKDIR /app/backend
COPY backend/ ./
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir \
        -i https://pypi.tuna.tsinghua.edu.cn/simple ./
COPY --from=web /app/web/dist ./app/static
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fs http://127.0.0.1:8000/api/health || exit 1
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

compose CLI 必须打进镜像:栈操作靠 shell out,这是硬依赖。运行入口统一 `app.main:app`(WORKDIR=/app/backend 源码树优先,避免 site-packages 双导入)。

## 运行挂载

### Linux 宿主机

```bash
docker run -d --name dockerapi \
  -p 8000:8000 \
  --restart unless-stopped \
  --group-add $(getent group docker | cut -d: -f3) \
  --read-only --tmpfs /tmp \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/stacks:/opt/stacks \
  -v dockerapi-data:/data \
  -e TZ=Asia/Shanghai \
  dockerapi:1.0.0
```

`--group-add` 取宿主机 docker 组 GID(每台机器不同);`--read-only` 时 `/data` 卷与 `/tmp` tmpfs 仍可写。

### Windows Docker Desktop(开发环境实测验证)

```powershell
docker run -d --name dockerapi `
  -p 8000:8000 `
  -v //./pipe/docker_engine:/var/run/docker.sock `
  -v C:/stacks:/mnt/host/c/stacks `
  -e STACKS_DIR=/mnt/host/c/stacks `
  -v dockerapi-data:/data `
  dockerapi:1.0.0
```

**与 Linux 的关键差异——栈目录路径语义**:

Docker Desktop 的 daemon 跑在 WSL2 虚拟机里,bind mount 源路径按**虚拟机视角**解析,Windows 的 `C:\stacks` 在 daemon 视角是 `/mnt/host/c/stacks`(2026-08 在 Docker Desktop 29.7.2 实测)。因此 Windows 上"路径内外一致"的对齐目标是 daemon 视角路径,不是 `/opt/stacks`:

- 挂载时直接对齐: `-v C:/stacks:/mnt/host/c/stacks` + `STACKS_DIR=/mnt/host/c/stacks`
- 栈内 compose 文件需要 bind mount 宿主目录时,统一写 `/mnt/host/c/...` 前缀路径
- **强烈建议栈内 compose 文件优先使用命名卷(named volume)而非 bind mount**——路径语义问题直接消失,Linux/Windows 行为完全一致

**实测确认的坑**:daemon 对不存在的 bind 源路径会**静默在虚拟机里创建空目录**,不报错。路径写错(比如用了 `/mnt/c/...` 这种看似合理的写法)时容器照常启动,但数据落在虚拟机的临时目录里,极易造成"看起来能跑、数据丢了"的假象。排查方法:在容器里 `ls` 挂载点确认内容,或 `docker inspect` 看 Mounts 的 Source。

其他差异:

- `--group-add <GID>` 授 socket 权限的做法**仅 Linux 适用**;Windows 命名管道由 Docker Desktop 代理,管道挂载实测可用,无需处理组权限
- daemon 不是系统服务,随 Docker Desktop(用户登录)启动——`--restart=always` 的容器在 Desktop 启动后才会拉起,无人值守场景需在 Docker Desktop 设置里开启开机自启
- Windows 路径的 bind mount 经过文件系统翻译层,大量小文件读写明显慢于 Linux 原生;对"读 compose 文件"这种场景无影响

### 三个挂载点各自职责

| 挂载 | 作用 | 缺失后果 |
|---|---|---|
| `docker.sock` | 连接宿主 daemon(DooD) | 所有 Docker 操作失败,仅剩登录 |
| `/opt/stacks` | compose 栈文件(路径内外一致) | 栈列表为空 |
| `/data` | SQLite + JWT 密钥 + 应用日志轮转文件 | 容器重建即丢失全部账号/key/审计(等于回到 setup 模式) |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | daemon 连接;可改 `tcp://`(建议 2376+TLS)或 `ssh://`,多主机留口 |
| `DATA_DIR` | `/data` | 持久化目录 |
| `STACKS_DIR` | `/opt/stacks` | compose 栈扫描目录 |
| `DOCKER_TIMEOUT` | 30 | 单次 daemon API 调用超时(秒) |
| `COMPOSE_JOB_TIMEOUT` | 1800 | compose 任务超时(秒) |
| `AUDIT_RETENTION_DAYS` | 90 | 审计保留天数 |
| `LOG_LEVEL` | INFO | 应用日志级别 |
| `TZ` | UTC | 时区 |

## HTTPS(可选)

**本项目基线: 内网部署,不暴露公网,HTTPS 不作为必须交付**。面板端口只绑内网/127.0.0.1 即为部署底线。将来若要暴露到更大网络,再启用前置反向代理终结 TLS:

- **推荐 Caddy**(自动签发续期证书),`deploy/Caddyfile` 提供模板:

```
dockerapi.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

- 面板端口只绑 `127.0.0.1` 或内网,禁止直接暴露公网
- SSE 需代理关闭缓冲(Caddy 默认支持流式,无需额外配置;若换 Nginx 需 `proxy_buffering off`)

## 安全清单(实现与评审时逐项核对)

- [ ] 无默认账号密码,首次启动强制 setup
- [ ] API Key 只存哈希,明文仅创建时返回一次
- [ ] 日志脱敏 filter 生效(dka_ 前缀/Bearer/密码字段)
- [ ] 登录限速: 同一 IP 连续失败 5 次锁定 60 秒
- [ ] SSE 日志流订阅上限(每容器 5 个)
- [ ] 不提供任意创建容器 API;容器只能经 compose 栈创建
- [ ] 不提供 exec 终端
- [ ] 审计覆盖全部变更操作,包括失败的
- [ ] 容器以非 root 运行(镜像加 `USER app`;socket 权限由部署方通过 `--group-add <docker GID>` 授予,部署文档注明)
- [ ] 镜像只读层 + 显式 tmpfs(`/tmp`),减小篡改面
- [ ] compose CLI 调用使用白名单环境变量(仅透传 `PATH`/`DOCKER_HOST`/`HOME`),不受请求参数污染
- [ ] 静态资源由 FastAPI 直接托管,`/api` 与静态路径分离,无路径穿越

## 备份与恢复

- 全部状态在 `/data`(SQLite + secret.key)与 `/opt/stacks`
- 备份 = 备这两个位置(SQLite 备份用 `sqlite3 .backup` 或直接停面板后 copy)
- 恢复 = 挂回同路径启动;换机器只需两个目录 + 镜像

## 环境实测记录

**2026-08-25 ｜ VMware 虚拟机 192.168.202.133 ｜ Ubuntu 26.04 LTS ｜ docker.io 29.1.3 + compose 2.40.3(apt 安装) ｜ daemon 拉取代理 192.168.5.123:7890(systemd drop-in)**

M0 四项设计假设全部实测通过:

| # | 假设 | 结果 |
|---|---|---|
| 1 | docker.sock 挂载进容器可操作宿主 daemon(DooD) | ✅ 容器内 `docker version` 连上宿主 29.1.3 |
| 2 | 非 root 用户 + `--group-add <GID>` 可获得 socket 权限 | ✅ 不加组 permission denied,加组后成功 |
| 3 | 容器内 compose CLI 经 socket 对宿主执行 up | ✅ demo 栈正常创建启动 |
| 4 | 路径一致技巧:相对路径 bind mount 落宿主机同路径 | ✅ `./data:/data` 落到宿主 `/opt/stacks/demo/data/marker.txt`,内容正确 |

部署注意: docker 组 GID **每台主机不同**(测试机为 103),部署脚本须用 `getent group docker` 动态获取,不可写死。测试栈 `/opt/stacks/demo` 保留作后续联调用样例。

**2026-08-25 实施验收(同机)**:

| 项 | 结果 |
|---|---|
| 单元测试 | 118 passed(本地 Win 与 VM 同绿) |
| 裸机服务集成测试(tests/integration_vm.py) | 29/29(含 setup→登录→key 生命周期→scope 拒绝→栈 up/down→容器幂等操作→审计→SSE) |
| 容器化部署(ubuntu:24.04 镜像,非 root + group_add 103 + 只读层) | 集成 27/27(2 项 setup 流程因账号已存在跳过);镜像内 compose v2.40.3 可用 |
| 数据持久化 | 容器彻底删除重建后账号/密钥库完好(volume dockerapi-data) |
| SSE 实时流 | job 流/日志流输出合法 JSON 帧 + end 事件,curl -N 实测 |
| SPA | 首页/history 回退/静态资源 200,未知 /api 路径统一 404 信封 |

实施期修复的关键问题(均已回归): docker-py 7.2.0 `logs()` 无 `demux` 参数(手动 8 字节流头解复用回退);**docker-py 7.2 `logs(stream=True)` 返回的 CancellableStream 已被 `_stream_helper` 自动解复用(产出无帧头 payload 且丢失 stdout/stderr 区分),流式回退必须走原始 HTTP 端点拿真帧**(开发后审计发现,已修+单测锁定);sse-starlette dict 帧 TypeError(路由边界统一 `{"data": json.dumps(...)}`);SSE 生成器内 404/429 在 200 响应头后不可达(转首帧 `event: error` 终结);FastAPI 0.141 路由表非扁平化(回归测试改 HTTP 探测式);Dockerfile 缺 `ENV DATA_DIR/STACKS_DIR` 导致只读根文件系统崩溃。
