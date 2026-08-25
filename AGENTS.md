# AGENTS.md — DockerAPI 协作代理说明

给在本仓库工作的 AI 编码代理(以及新加入的人类)看的入门说明。项目当前处于**设计定稿、待实现**阶段,实现任何功能前先读 `docs/` 对应文档。

## 项目是什么

DockerAPI(v1.0.0):跑在容器里的 Docker 管理平台。挂载宿主机 `docker.sock`(DooD 模式),提供人类用的 Web 管理界面和程序用的 REST API(带 scope 权限的 API Key)。容器只能通过 `/opt/stacks` 下的 compose 栈创建,不提供任意创建。

- 技术栈: FastAPI + docker-py + SQLite ｜ Vue3 + Soybean Admin
- 设计文档: `docs/`(架构 `architecture.md`、认证 `auth.md`、API `api.md`、数据 `data-model.md`、日志 `logging.md`、前端 `frontend.md`、部署 `deployment.md`、路线图 `roadmap.md`)

## 目录

```
DockerAPI/
├── AGENTS.md          # 本文件
├── docs/              # 设计文档(单一事实来源)
├── backend/           # FastAPI 后端(待建,结构见 architecture.md)
├── web/               # Soybean Admin 前端(待建)
├── deploy/            # Dockerfile / Caddyfile / 示例 compose(待建)
└── 参考资料/           # docker-py、EasyDockerWeb 克隆,只读参考,禁止修改/提交
```

## 硬性规则(违反即 bug)

1. **安全红线**
   - 禁止添加"任意参数创建容器"类端点;容器创建只能走 compose 栈
   - 禁止添加 exec/终端类端点(v1 明确不做)
   - API Key 明文只存内存、只在创建响应出现一次;数据库/日志/审计中永不明文出现
   - 日志永不输出:API Key 明文、密码、完整 JWT;loguru 脱敏 filter 必须全程挂载
   - 新增端点必须标注所需 scope 并走依赖校验;认证豁免仅 `login`/`setup`/`/api/health` 三处(另: `/docs`、`/openapi.json`、`/redoc` 为内网部署下的有意豁免——为 APP 调用方提供交互文档,2026-08-25 裁决)
   - 容器详情接口必须做 env 脱敏(规则见 api.md)
2. **语义红线**
   - daemon 返回 304 一律按成功翻译(`already_in_state`),保证操作幂等
   - 不做 check-then-act:直接下发操作按结果翻译(404→404,409→409,5xx→502)
   - docker-py 是同步库,任何调用必须 `asyncio.to_thread` 桥接,禁止在 async 路径直接调用
   - 栈名必须匹配 `^[a-z0-9][a-z0-9_-]{0,63}$` 且 resolve 后仍在 STACKS_DIR 内;compose 执行必须显式 `-p <name>`
   - keys 写操作(增删改)仅接受 JWT 用户主体,API Key 主体一律 403
   - 不引入"容器状态内存表";内存中只允许 per-container 锁和 stats 环形缓冲
   - 面板自身容器的变更操作一律 403 self_protection(容器内通过 hostname 识别自己),列表/详情带 is_self 标记
   - 容器操作审计在路由层显式调用 audit_svc;栈操作审计由 JobMgr 在任务终态写入
3. **改动流程**
   - 改设计先改 `docs/` 再改代码,文档与实现不一致以最新提交的 docs 为准并同步修复
   - `参考资料/` 目录是第三方参考代码,禁止修改、禁止纳入构建

## 约定

- Python: 3.12,类型标注全覆盖,ruff(format + lint);SQLAlchemy 2.x 风格
- 前端: 跟随 Soybean Admin 既有风格(ESLint + Prettierrc 已带),组件放 `src/components/custom/`
- API 路径一律 `/api/v1` 前缀(`health` 例外);错误响应统一 `{"error": {"code", "message"}}`
- 时间一律 ISO 8601 UTC 存 TEXT
- 数据库变更优先改 `docs/data-model.md`;v1 无 Alembic,开发期可删库重建,发布后引入

## 常用命令(实现后生效)

```bash
# 后端开发(M1 后可用)
cd backend && pip install -e . && uvicorn app.main:app --reload --port 8000
pytest                                  # 单测

# 前端开发
cd web && pnpm install && pnpm dev      # 5173,代理 /api → 8000
pnpm build                              # 产物 web/dist

# 本地跑完整面板(deploy/ 就绪后)
docker compose -f deploy/docker-compose.example.yml up -d
```

## 实现顺序

按 `docs/roadmap.md` 的 M1→M6 里程碑推进,不要跳步:M1 认证未闭环前不动容器 API,M4 任务系统未就绪前不给栈操作接前端按钮。
