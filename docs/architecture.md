# 架构与技术栈

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端框架 | Python 3.12 + FastAPI | 自动 OpenAPI 文档(`/docs`)直接服务"其他 APP 通过 API 调用"的场景;依赖注入做 scope 校验非常干净 |
| Docker 客户端 | docker-py | 分层清晰,异常体系统一(`NotFound`/`APIError` 携带 daemon 的 HTTP 状态码),可直接映射为 API 响应 |
| 数据库 | SQLite(SQLAlchemy) | 单文件零运维,量级(单管理员 + 少量 API Key + 审计流)完全够用 |
| 前端 | Vue 3 + TypeScript + Soybean Admin 模板 | 模板自带登录页/布局/暗色主题,监控面板气质契合 |
| 运行时 | uvicorn,单进程 | 并发规模小(2~3 个调用方),无需多 worker |
| Compose 执行 | 容器内 shell out 到 `docker compose` CLI | docker-py/dockerode 均不支持 compose;Portainer/Dockge 同款做法 |

依赖库(后端): `fastapi` `uvicorn` `docker`(docker-py) `sqlalchemy` `bcrypt` `pyjwt` `loguru` `sse-starlette`(日志/任务输出流式推送)

## 总体架构

```
┌─ Web 浏览器 ────────────┐   ┌─ 其他 APP ───────────────┐
│  Soybean Admin 前端      │   │  API Key (Bearer token)  │
└───────────┬─────────────┘   └───────────┬──────────────┘
            │ JWT                          │ API Key
            ▼                              ▼
┌─────────────────────────────────────────────────────────┐
│  DockerAPI 容器 (FastAPI 单进程)                          │
│                                                          │
│  中间件链: 认证(JWT/APIKey) → scope 校验 → 限速 → 请求日志 │
│  (审计不进中间件: 操作处理路径显式调用 services/audit_svc)  │
│                                                          │
│  路由层: auth / containers / stacks / jobs / keys /      │
│          audit / system                                  │
│                                                          │
│  Service 层:                                             │
│   ContainerSvc  ── docker-py ──┐                         │
│   ComposeSvc    ── subprocess(cli) ──┐                   │
│   JobMgr        ── 任务队列/缓冲       │                   │
│   LogSvc        ── 日志流 demux       │                   │
│   StatsSampler  ── 定时采样环形缓冲    │                   │
│   AuditSvc      ── 审计落库           │                   │
│                                                          │
│  SQLite (/data/dockerapi.db)                             │
└──────────────┬──────────────────────────┬────────────────┘
               │                          │
     /var/run/docker.sock        /opt/stacks(路径内外一致)
               │                          │
               ▼                          ▼
        宿主机 dockerd              宿主机上的 compose 文件
```

## 核心设计决策

### 1. DooD: 挂载 socket,不做 DinD

挂载 `/var/run/docker.sock`(Windows Docker Desktop 用 `//./pipe/docker_engine:/var/run/docker.sock`)。daemon 在宿主机上,所有操作直接作用于宿主机资源。**不需要 privileged**,只需要 socket 挂载 + socket 权限。

### 2. daemon 是唯一状态源,不做内存状态表

正确性依赖 daemon:每次读操作直接查 daemon(本地 socket,毫秒级)。**不维护容器状态内存表做操作决策**——缓存会因容器自崩、宿主机直接敲 docker 命令、compose 重新部署等原因变脏,基于脏状态做决定比没有状态更糟。

唯一允许的内存结构:
- **per-container 操作锁**: 串行化同一容器的并发操作(`dict[str, asyncio.Lock]`,按需创建,用完可清理)
- **stats 采样环形缓冲**: 纯读优化,丢了不影响正确性

前端刷新用 3~5 秒轮询,不订阅 events 流(客户端多了再启用,Service 层预留空壳)。

### 3. 操作幂等,而非阻止并发

Docker daemon 对重复操作有确定语义:启动已运行的容器返回 **304**,停止已停止的容器返回 **304**。API 层将 304 翻译为成功(`200`),重复请求天然无害。**不做 check-then-act,只做 act-then-interpret**:直接下发操作,按 daemon 响应翻译结果。

### 4. Compose 通过 CLI 子进程执行

- 容器镜像内安装 `docker-cli` + `docker-compose-plugin`
- 栈目录挂载时**容器内路径与 daemon 视角路径一致**,compose 文件内的相对路径/卷挂载路径语义才不乱。Linux 宿主机上就是 `/opt/stacks`;Windows Docker Desktop 上 daemon 跑在 WSL2 虚拟机里,对齐路径是 `/mnt/host/c/stacks`(实测,详见 deployment.md);栈内 compose 文件优先用命名卷可完全绕开此问题
- 栈名 = `/opt/stacks/<name>/` 目录名,须匹配 `^[a-z0-9][a-z0-9_-]{0,63}$`(小写 slug),且 `Path.resolve()` 后必须仍在 `STACKS_DIR` 内(防路径穿越,白名单式校验)
- compose 文件识别优先级: `compose.yaml` > `compose.yml` > `docker-compose.yaml` > `docker-compose.yml`;无 compose 文件的目录跳过不报错
- 执行 compose 一律显式传 `-p <name>`(项目名=目录名 slug),避免 compose 默认规范化(大写转小写等)导致标签反查失配
- 执行 `docker compose -f <file> -p <name> up -d`,cwd=栈目录,输出实时写入任务缓冲区,超时默认 30 分钟
- 同一栈的 compose 操作串行执行;全局并发 compose 任务上限 3

### 5. 长任务异步化(202 + Job)

`compose up` 可能带镜像拉取跑几分钟,同步 HTTP 必超时。模式:

```
POST /api/v1/stacks/{name}/up
→ 202 Accepted {"job_id": "j_xxx"}
→ GET  /api/v1/jobs/{job_id}      # status + output(滚动缓冲)
→ GET  /api/v1/jobs/{job_id}/stream  # SSE 实时输出(前端用)
```

容器 start/stop 等秒级操作不走 Job,直接同步返回。

### 6. 容器创建只走 compose,不开放自由创建

v1 **不提供**"任意参数创建容器"的 API。这是最重要的安全红线:privileged、挂载宿主机敏感路径、`--pid=host` 等危险操作全部从根上不存在。要新增容器 = 在宿主机 `/opt/stacks` 放 compose 文件。

### 7. 连接配置

学 docker-py 的 `from_env()` 思路:默认 `unix:///var/run/docker.sock`,允许环境变量 `DOCKER_HOST` 覆盖为 `tcp://`(建议 2376+TLS)或 `ssh://`。为多主机留口子,v1 只实现单 host。

### 8. docker-py 是同步库,异步进程里必须线程桥接(硬性规则)

docker-py 是纯同步 HTTP 客户端,直接在 async 路由里调用会阻塞事件循环——daemon 卡住 30s 时整个面板(含 SSE 流、健康检查)冻结。因此:

- 所有 docker-py 调用一律 `await asyncio.to_thread(...)`
- `logs(stream=True, follow=True)` 等阻塞迭代放入线程逐帧投递回 `asyncio.Queue`
- 后台采样器单帧失败只记 debug 跳过,不影响循环

### 9. 异步栈操作的审计时序

`stack.up/down/restart` 是 202 异步任务:提交时**不写审计**,只记应用日志;任务到达终态时由 JobMgr 写审计行(done→`success`,failed→`error`,timeout→`error` 且动作记 `job.timeout`),detail 携带 job_id,actor 在提交时捕获后随任务传递。

依赖注记: job id 的 ULID 由 `app/util.py` 自实现(Crockford Base32),不引入第三方库。

## 目录结构(规划)

```
DockerAPI/
├── AGENTS.md               # AI 协作代理说明
├── docs/                   # 本设计文档
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py         # FastAPI 入口,静态文件托管
│   │   ├── config.py       # 环境变量配置
│   │   ├── db.py           # SQLAlchemy engine/session
│   │   ├── auth/           # jwt_utils / apikey / deps(认证依赖)
│   │   ├── middleware/     # request_log.py / rate_limit.py
│   │   ├── routers/        # auth / containers / stacks / jobs / keys / audit / system
│   │   ├── services/       # docker_client.py / container_svc.py / compose_svc.py
│   │   │                   # job_mgr.py / log_svc.py / stats_sampler.py / audit_svc.py
│   │   ├── models/         # SQLAlchemy 表定义
│   │   └── static/         # 前端构建产物(git 不跟踪)
│   └── tests/
├── web/                    # Soybean Admin 前端源码
└── deploy/
    ├── Dockerfile
    ├── Caddyfile           # 可选 HTTPS 反代
    └── docker-compose.example.yml
```
