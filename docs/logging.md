# 日志系统

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25

日志系统分三块,各自独立: **容器日志**(看被管容器)、**应用运行日志**(面板自身)、**审计日志**(谁做了什么,定义见 data-model.md,本文只写记录机制)。

## 1. 容器日志(看被管容器)

### 一次性拉取

`GET /api/v1/containers/{id}/logs?tail=200&timestamps=false`

- Service 层调 docker-py `container.logs(tail=N, timestamps=...)`
- 响应: `{"lines": [{"stream": "stdout", "line": "...", "ts": null}]}`;`timestamps=false` 时 ts 为 null
- docker-py 返回的多路复用原始流在 Service 层完成 **demux**(stdout/stderr 分离),`stream=False` 时用 `demux=True` 参数

### 实时流(SSE)

`GET /api/v1/containers/{id}/logs/stream`

```
后端: docker-py logs(stream=True, follow=True) → demux → 逐帧推送
事件: data: {"stream": "stdout", "line": "..."}
结束: 容器停止/删除导致流关闭 → event: end → 连接关闭
异常: daemon 断开 → event: error + data: {message}
```

- 客户端断开(Request 断连)→ 立即 `close()` daemon 流,不留孤儿订阅
- 无认证回放限制:一个容器同时最多 5 个流订阅,超出返回 `429 too_many_streams`(防 key 泄露后被恶意拉流耗资源)
- 栈聚合日志 `GET /stacks/{name}/logs`:并发拉取该栈全部容器最近 N 行,按时间戳归并排序(timestamps 强制开启)

### 展示层

前端日志查看器:暗色等宽字体,stdout/stderr 按颜色区分,自动滚底(用户上滚时暂停跟随),检索框客户端过滤,v1 不做服务端检索。

## 2. 应用运行日志(面板自身)

- 库: loguru
- 输出: **stdout**(容器最佳实践,docker logs 可看)+ 文件 `/data/logs/dockerapi.log`
- 轮转: 10MB × 5 个文件
- 格式: 结构化单行 JSON(`ts / level / logger / msg / 请求上下文`)

### 请求日志(中间件)

每个 HTTP/WS 请求记一条 info:

```
{method, path, status, duration_ms, actor_type, actor_name, ip}
```

- 响应体不记;`Authorization` 头不记;登录接口的请求体不记
- SSE/WS 长连接记录建立一条(2026-08-25 裁决: 断开不单独记日志,靠订阅计数与连接关闭事件排障),不按帧记

### 日志分级约定

| 级别 | 用途 |
|---|---|
| DEBUG | 开发诊断,生产默认 INFO |
| INFO | 请求日志、任务开始/结束、采样器启停 |
| WARNING | 认证失败、无效 API Key、流订阅数达上限、daemon 调用超时后重试 |
| ERROR | daemon 5xx、compose CLI 非零退出、未捕获异常(含堆栈) |

**红线**: 任何级别的日志中都不得出现 API Key 明文、密码明文、JWT 完整值。loguru 统一挂脱敏 filter,正则匹配 `dka_[A-Za-z0-9]+` / `Bearer ...` / `password` 字段做替换;filter 作用于消息文本(2026-08-25 裁决: 异常堆栈文本不强制过滤,当前所有调用点均不携带密钥参数,新增日志调用须保持此约束)。

## 3. 审计日志记录机制

- 记录点放在**操作处理路径**(路由层/任务管理器)显式调用 `services/audit_svc.record(...)`,保证语义清晰且不依赖全局中间件;容器操作在路由层记录,栈操作由 JobMgr 在任务终态记录(见 architecture.md 决策 9)
- 路由层从认证依赖注入的主体(request.state.principal)取得 actor
- 写入失败(磁盘满等)不阻断业务操作,但记 ERROR 到运行日志——审计写失败必须显式可见
- `result=error` 时 detail 存 daemon 错误摘要(截断 500 字符)

## 4. 与 Job 的关系

compose CLI 的输出属于 Job 记录(`jobs.output`),不进应用日志;JobMgr 只在应用日志里记任务的 started/finished/timeout 摘要。两者读者不同:Job 输出给用户看进度,应用日志给运维排障。
