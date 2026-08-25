# REST API 规范

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25
- 基础路径: `/api/v1`(为调用方预留版本演进空间)
- 交互文档: FastAPI 自动生成 `/docs`(Swagger UI)

## 通用约定

### 认证

- 程序调用: `Authorization: Bearer dka_xxx`
- Web 前端: 同样走 Bearer(JWT)
- 豁免端点仅 `login` / `setup` / `/api/health`(见 auth.md)

### 统一错误格式

所有非 2xx 响应体:

```json
{
  "error": {
    "code": "container_not_found",
    "message": "No such container: abc123"
  }
}
```

`message` 面向人,可直接展示;`code` 面向程序,稳定不变。

### 分页

jobs 与 audit 列表分页 `?page=1&page_size=20`(page_size 上限 100),响应带 `total`。containers/stacks/keys 按量级设计返回全量(裸数组或 `{total,items}`,2026-08-25 裁决: 面板规模无分页必要)。

### 时间格式

ISO 8601 UTC,如 `2026-08-25T03:30:00Z`。

## 错误码翻译表(daemon → DockerAPI)

核心原则:**act-then-interpret**——不做预检查,直接操作,按 daemon 响应翻译。304 翻译为成功,使所有变更操作天然幂等。

| daemon 状态 | 场景 | DockerAPI 响应 |
|---|---|---|
| 304 | 启动已运行的容器 / 停止已停止的容器 | `200 {"status": "ok", "note": "already_in_state"}` |
| 404 | 容器/栈不存在 | 404 `container_not_found` / `stack_not_found` |
| 409 | 删除运行中的容器(未 force)/ 冲突 | 409 `conflict` + daemon message |
| 409 | 容器正在删除等暂态冲突 | 409 `conflict`,message 提示稍后重试 |
| 5xx | daemon 内部错误 | 502 `daemon_error` |
| 超时 | daemon 30s 无响应 | 504 `daemon_timeout` |

## 端点总表

### auth — 认证

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| POST | `/auth/login` | 豁免 | 用户名密码 → `{token, expires_at}` |
| POST | `/auth/setup` | 豁免(仅 setup 模式) | 创建管理员 |
| GET | `/auth/me` | 任意已认证 | 当前主体 `{type: user|api_key, name, scopes:[...]}` |
| PATCH | `/auth/password` | 任意已认证(**仅 JWT 用户**,key 主体 403) | 修改自身密码(需验证 old_password);v1 不做 JWT 全局吊销,改密后旧 token 有效至自然过期 |

### containers — 容器

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/containers` | view | 容器列表(含 compose 分组标签、状态排序、内嵌最近 30 点 stats) |
| GET | `/containers/{id}` | view | 详情(inspect,env 按 masking 规则脱敏) |
| POST | `/containers/{id}/start` | start | 启动(幂等) |
| POST | `/containers/{id}/stop` | stop | 停止,`?t=10` 超时秒数(幂等) |
| POST | `/containers/{id}/restart` | start | 重启,`?t=10` |
| POST | `/containers/{id}/pause` | stop | 暂停 |
| POST | `/containers/{id}/unpause` | start | 恢复 |
| DELETE | `/containers/{id}` | delete | 删除,`?force=false` 运行中且非 force 时 daemon 返回 409 → 透传 |
| GET | `/containers/{id}/logs` | view | `?tail=200&timestamps=false`,一次性返回文本 |
| GET | `/containers/{id}/logs/stream` | view | SSE 实时日志,断开即取消订阅 |
| GET | `/containers/{id}/stats` | view | 近 1h 采样序列,可选 `?since=<iso>` 过滤(见 stats 采样) |

`{id}` 接受完整容器 ID 或容器名(daemon 原生支持);短 ID 前缀由 Service 层先查容器列表解析成完整 ID 再透传。

**列表项字段(响应契约)**:

```json
{
  "id": "abc123...", "name": "demo-demo-1", "image": "busybox:latest",
  "state": "running", "compose_project": "demo",
  "created": "2026-08-25T03:00:00Z", "is_self": false,
  "stats": [{"ts": "...", "cpu_percent": 1.2, "mem_mb": 3.4, "mem_limit_mb": 976.0}]
}
```

`stats` 为内存环形缓冲中最近 30 点(容器无缓冲时为空数组,前端按无图处理);`is_self` 标记该项是否为面板自身容器(前端据此禁用变更按钮)。

**自身容器保护**: 面板自身容器的所有变更操作(start/stop/restart/pause/unpause/remove)一律返回 `403 self_protection`(面板运行在容器内时通过 hostname=容器短 ID 自动识别;裸机运行时无此保护对象)。识别方式对调用方透明,仅体现在 `is_self` 字段与 403 拒绝上。

### stacks — Compose 栈

**栈名校验(安全底线,不可省)**: `{name}` 必须匹配 `^[a-z0-9][a-z0-9_-]{0,63}$`,且解析后路径必须仍在 `STACKS_DIR` 内;名称必须与目录扫描结果精确匹配(白名单式)。不合法一律 404 `stack_not_found`。

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/stacks` | view | 扫描 `/opt/stacks` 目录,返回栈列表 + 状态 |
| GET | `/stacks/{name}` | view | 栈详情:compose 文件内容 + 关联容器列表 |
| POST | `/stacks/{name}/up` | start | `202 + {job_id}`(异步) |
| POST | `/stacks/{name}/down` | stop(`?volumes=true` 时**另需 delete**) | `202 + {job_id}`,`?volumes=false` 是否连带删卷 |
| POST | `/stacks/{name}/restart` | start | `202 + {job_id}` |
| GET | `/stacks/{name}/logs` | view | `?tail=200`,聚合该栈全部容器日志(按时间排序) |

栈状态判定: 标签 `com.docker.compose.project == 栈名`(执行时显式 `-p <name>`,项目名与目录名一致)的容器集合 → 全部 running 为 `running`,部分为 `partial`,无容器为 `not_created`,有容器但全停为 `stopped`。

栈操作审计时序: 提交时不写审计,任务终态时由 JobMgr 写(见 architecture.md 决策 9)。

### jobs — 长任务

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/jobs` | view | 任务列表(分页,默认最近 20) |
| GET | `/jobs/{job_id}` | view | `{status, exit_code, output, started_at, finished_at}` |
| GET | `/jobs/{job_id}/stream` | view | SSE 实时输出,任务结束自动关闭 |

### keys — API Key 管理(读:admin scope;写:仅 JWT 用户主体)

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/keys` | admin | 列表(key_prefix + scopes + enabled + last_used_at) |
| POST | `/keys` | admin + **JWT 用户** | 创建,响应含**一次性明文** `{id, key: "dka_..."}` |
| PATCH | `/keys/{id}` | admin + **JWT 用户** | 修改名称/scope/启停 |
| DELETE | `/keys/{id}` | admin + **JWT 用户** | 硬删除 |

### audit — 审计日志

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/audit` | admin | 分页 + 过滤(actor / action / target / 时间范围) |

### system — 系统

| 方法 | 路径 | scope | 说明 |
|---|---|---|---|
| GET | `/version` | view | 面板版本 + daemon 版本 + daemon info 摘要(`storage_driver`/`images_count`/`volumes_count`/`containers_summary{running,paused,stopped,all}`) + 连接方式 |
| GET | `/health`(注意:在 `/api` 下,无版本前缀) | 豁免 | `{"status": "ok", "db": true}`,HEALTHCHECK 探针 |

## 关键行为细则

### 容器操作串行化

同一容器的变更操作在 Service 层用 per-container `asyncio.Lock` 串行;不同容器互不阻塞。锁等待超过 10s 返回 `409 conflict`(message: 操作正在被其他请求执行)。compose 栈操作同理按栈名串行,全局并发 compose 任务上限 3,超出排队。

### 容器详情脱敏(masking)

inspect 返回的 `Config.Env` 中,变量名匹配 `(?i)(PASS|SECRET|TOKEN|KEY|CREDENTIAL)` 的值替换为 `***`;`Labels` 原样保留。日志流不做脱敏(日志内容不可控,文档中明示)。

### stats 采样

- 后台采样器每 10s 对全部 running 容器各取一次 stats
- 每容器保留最近 1h 环形缓冲(360 点),仅内存,重启即清
- 采样点结构: `{"ts": "<iso>", "cpu_percent": <float>, "mem_mb": <float>, "mem_limit_mb": <float>}`
- `GET /containers/{id}/stats` 返回缓冲序列(可选 `?since=<iso>` 过滤);容器不在缓冲中(刚启动/一直停止)返回当前单点或空数组
- 首次请求时缓冲为空的容器,采一帧立即返回

### SSE 流式接口约定

- 响应 `Content-Type: text/event-stream`
- 日志流: 每帧 `data: {"stream": "stdout", "line": "..."}`;**`?tail=N` 限制打开时的历史回放量**(默认 200,每次重连都会回放,客户端应在连接建立后清空本地缓冲再接收);daemon 的多路复用流在服务端完成 stdout/stderr demux
- 任务流: 每帧 `data: {"chunk": "..."}`
- **错误即终点**: 服务端在流开始后才判定的业务错误(容器不存在、订阅超限 429、daemon 故障)以首帧 `event: error` + `data: {"code","message","status"}` 下发,客户端收到后**不得重连**
- 客户端断开 → 服务端立即取消对 daemon 流的订阅(不留孤儿连接);并发订阅上限: 同容器 5 路、全局 20 路
- 容器删除/停止导致的流结束 → 发送 `event: end` 后正常关闭

### 请求体大小与参数校验

- 请求体上限 1MB(Content-Length 预检,超限 413 `payload_too_large`)
- 所有查询参数由 Pydantic 严格校验,非法值返回 `422 validation_error`(错误结构不含请求原值);操作类参数带范围(如 stop/restart 的 `t` ∈ [0,600],logs 的 `tail` ∈ [1,10000])

## 调用示例

```
# 获取容器列表
curl -H "Authorization: Bearer dka_xxx" https://host/api/v1/containers

# 启动容器(重复调用安全)
curl -X POST -H "Authorization: Bearer dka_xxx..." \
     https://host/api/v1/containers/myapp/start
# → 200 {"status": "ok"}          首次启动成功
# → 200 {"status": "ok", "note": "already_in_state"}   已在运行,同样算成功

# 启动 compose 栈(异步)
curl -X POST -H "Authorization: Bearer dka_xxx..." \
     https://host/api/v1/stacks/myapp/up
# → 202 {"job_id": "j_01HZ..."}

# 查询任务进度
curl -H "Authorization: Bearer dka..." https://host/api/v1/jobs/j_01HZ...
# → {"status": "running", "exit_code": null, "output": "Pulling nginx..."}
```
