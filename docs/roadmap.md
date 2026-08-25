# 实施路线图

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25

## 实施顺序(里程碑)

> **2026-08-25 实施完成**: M1~M6 全部交付并通过验收(118 单测 + VM 裸机集成 29 项 + 容器化集成 27 项 + 数据持久化/重建验证)。开发方式: 骨架定接口 + 5 个并行 subagent 开发 + 双向审计循环(文档前置审计 17 项裁决、开发后安全/正确性双审计)。

### M0 — Linux 验证环境(VMware 虚拟机) ✅ 2026-08-25 完成

- 交付物: Ubuntu/Debian 虚拟机 + 原生 Docker Engine + compose 插件,SSH 可达,打快照
- 在真机 Linux 上实测设计假设: docker.sock 挂载、非 root + `--group-add` 权限模型、`/opt/stacks` 路径一致技巧(compose 相对路径 bind mount 落宿主机)、容器内 compose CLI 经 socket 操作宿主机
- 此后该 VM 作为常驻测试环境,后续每个里程碑构建部署至此验收

**完成标志**: 上述四项假设全部实测通过,结论回写 deployment.md(Windows Desktop 章节降级为开发机参考)。

### M1 — 骨架与认证 ✅ 2026-08-25 完成

- 后端项目骨架: FastAPI 入口 / config / SQLAlchemy + SQLite / loguru + 请求日志中间件
- 双通道认证全链路: setup 模式 → 登录 → JWT → API Key 依赖注入 → scope 校验
- keys CRUD(含一次性明文返回)
- 前端: Soybean Admin 拉起,登录页 + 首次引导页打通,路由守卫

**完成标志**: 能登录,能创建/禁用 API Key 并用它调通一个受保护的演示端点。

### M2 — 容器 API 与审计 ✅ 2026-08-25 完成

- docker-py 接入(DOCKER_HOST 配置化、超时、错误翻译层、to_thread 桥接)
- 容器列表/详情(含脱敏)/start/stop/restart/pause/unpause/delete,幂等语义 + per-container 锁
- 审计落库(路由层显式调用 audit_svc,含失败记录)与 audit 查询端点
- `/health` `/version`

**完成标志**: API Key 按 scope 分别调通各操作;重复操作返回 already_in_state;全部动作落审计。

### M3 — 日志与监控 ✅ 2026-08-25 完成

- 容器日志:一次性 + SSE 实时流(demux、订阅上限、断开清理)
- stats 采样器(10s 环形缓冲)+ `/stats` 端点

**完成标志**: SSE 流在容器停止时正常收尾;断开客户端后 daemon 流即关。

### M4 — Compose 栈与任务系统 ✅ 2026-08-25 完成

- 栈扫描 / 状态判定 / 详情(compose 文件内容 + 关联容器)
- JobMgr: 202 + job_id、per-栈串行、全局并发上限 3、滚动输出缓冲、超时、清理
- `docker compose` 子进程封装(up/down/restart、白名单环境变量)
- 栈聚合日志

**完成标志**: compose up 大输出任务全程可见进度,超时任务标记 timeout 并审计。

### M5 — 前端页面补全 ✅ 2026-08-25 完成

- 容器列表/详情(含日志查看器、迷你图)/栈卡片/任务抽屉/审计页/设置页
- 5s 轮询、SSE 重连、操作反馈全套交互约定落地

### M6 — 容器化与交付 ✅ 2026-08-25 完成

- 多阶段 Dockerfile(ubuntu:24.04 基础,见 deployment.md)、非 root、HEALTHCHECK、只读层
- **镜像构建冒烟前置**: 正式交付前先构建一次,验证容器内 `docker compose version` 可用
- deploy/ 目录: Caddyfile、docker-compose.example.yml(含 group_add)、部署文档
- 安全清单(deployment.md)逐项核对

**完成标志**: 宿主机两条命令(Linux/Windows 各一)跑起完整面板,重建容器数据不丢。

## 各阶段测试要点

| 里程碑 | 关键测试 |
|---|---|
| M1 | 认证豁免只有 3 端点;禁用 key 立即失效;scope 越权返回 403 |
| M2 | 并发重复 start 语义;404/409 翻译;审计含失败记录 |
| M3 | 多客户端同订;容器删除时流收尾;订阅上限 429 |
| M4 | 同栈并发 up 只跑一个;输出超 256KB 截头;CLI 超时标记 |
| M6 | 重建容器后账号/密钥/审计还在;socket 未挂载时优雅降级提示 |

## 明确延后(不进 v1)

| 事项 | 延后理由 | 将来方案 |
|---|---|---|
| Web 终端(exec) | 攻击面最大 | 仅管理员 + 单独审计 + 会话录制 |
| 镜像管理 | compose up 自动拉取已覆盖需求 | list/remove/pull + 拉取任务复用 JobMgr |
| events 流式推送 | 2~3 调用方轮询无压力 | Service 层预留订阅空壳,客户端多时启用 |
| 多用户 | 单管理员场景 | users 表已留结构,加角色页即可 |
| 多主机 | 超出 v1 范围 | DOCKER_HOST 已留口,多连接池 + host 字段贯穿 |
| 服务端日志检索 | v1 客户端过滤够用 | 需要时引入 SQLite FTS 或接 Loki |
