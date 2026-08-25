# 前端设计

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25

## 技术选型

- **模板**: [Soybean Admin](https://github.com/soybeanjs/soybean-admin)(Vue 3 + TypeScript + Naive UI + UnoCSS + Vite)
- 选择理由: 颜值高、自带登录页/布局/路由权限/暗色主题,监控面板气质契合;暗色模式对运维工具是刚需
- 构建产物为纯静态文件,由 FastAPI 托管(`app/static`),前后端同源,**无需处理 CORS**
- 开发期: `pnpm dev` 起 Vite(5173),代理 `/api` 到本地 8000

## 页面清单

| 路由 | 页面 | 内容 | 数据源 |
|---|---|---|---|
| `/login` | 登录 | 用户名密码;检测到 `setup_required` 自动切首次引导表单 | `/auth/login` `/auth/setup` |
| `/dashboard` | 总览 | daemon 信息(版本/存储驱动/镜像数/容器数/卷数)、面板版本、运行中容器统计卡片 | `/version` + `/containers` |
| `/containers` | 容器 | 列表(状态徽章、名称、镜像、compose 分组、运行时长、CPU/内存迷你图);操作列:启动/停止/重启/暂停/恢复/删除;删除需二次确认(force 可勾选) | `/containers` 轮询 |
| `/containers/:id` | 容器详情 | 基本信息(端口/挂载/重启策略/健康状态)、环境变量(后端已脱敏,前端照常展示)、标签、日志页签(一次性 + 实时 SSE)、stats 曲线 | `/containers/{id}` `/logs` `/logs/stream` `/stats` |
| `/stacks` | Compose 栈 | 栈卡片(名称、状态徽章、容器数、compose 文件预览);操作:up/down/重启;up 后自动跳任务抽屉看实时输出 | `/stacks` 轮询 |
| `/jobs` | 任务中心 | 任务列表(类型/栈/状态/耗时),点击看输出 | `/jobs` 轮询 + `/jobs/{id}/stream` |
| `/keys` | API Key | 管理员页;列表(prefix/scopes/enabled/最近使用);创建弹窗(名称+scope 勾选);创建成功一次性展示明文+复制按钮 | `/keys` |
| `/audit` | 审计日志 | 管理员页;表格 + 过滤(actor/action/时间范围),分页 | `/audit` |
| `/manual` | 说明书 | 静态文档页: 快速开始/Compose 栈配置/API 调用规范/部署配置/开发信息/安全说明 | 无(静态) |
| `/settings` | 设置 | 管理员页;修改密码;连接信息展示(只读) | `/auth/me` 等 |

## 交互与状态约定

### 刷新策略

- 容器列表、栈列表: **5 秒轮询**(页面不可见时用 `visibilitychange` 暂停)
- 详情页 stats 曲线: SSE 思路保留,但 v1 直接并入 5 秒轮询 `/stats` 增量取新点即可(实现简单,量级无压力)
- 日志实时页: SSE(`/logs/stream`),断线自动重连(指数退避,上限 30s)
- 任务输出: SSE(`/jobs/{id}/stream`),任务终结后停止
- **SSE 客户端实现**: 必须用 fetch 流式方案(`@microsoft/fetch-event-source`)而非原生 `EventSource`(后者无法带 Authorization 头);**禁止** `?token=` 查询参数传凭证(会进访问日志)

### 操作反馈

- 所有变更操作按钮带 loading + 二次确认(删除、栈 down)
- 响应 `200 already_in_state` 提示"已在目标状态"(info 级,不算错误)
- `409/404` 弹 daemon 返回的 message 原文
- 操作成功后立即手动触发一次列表刷新,不等下一个轮询周期

### 认证处理

- JWT 存 localStorage,401 统一拦截 → 清除并跳登录页
- `503 setup_required` 统一拦截 → 跳首次引导页

### 组件复用

- `ContainerStateBadge`: created/running/paused/exited/dead + 颜色映射
- `MiniChart`: CPU%/内存 sparkline(基于采样序列)
- `LogViewer`: 等宽暗色、stdout 蓝/stderr 红、自动滚底 + 上滚暂停跟随、客户端过滤框;容器日志与任务输出共用
- `JobDrawer`: 任务实时输出抽屉,栈页与任务中心共用

## 构建与部署

- `pnpm build` → `web/dist` → Dockerfile 中拷入 `backend/app/static`
- FastAPI: `StaticFiles` 挂载 `/assets` 等资源,其余路径 fallback 到 `index.html`(SPA history 路由)
- 版本号注入: 构建时读 package.json 写入 `__APP_VERSION__`,`/version` 对比可发现前后端不同步
