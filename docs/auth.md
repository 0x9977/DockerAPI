# 认证与权限

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25

## 双通道认证模型

| 通道 | 使用者 | 凭证 | 获取方式 | 权限 |
|---|---|---|---|---|
| Web 会话 | 人类 | JWT(HS256,24h 有效) | `POST /api/v1/auth/login` | 管理员全量 scope |
| API Key | 程序 | `Authorization: Bearer dka_xxx` | 管理员在 Web 上创建 | 按 key 配置的 scope 子集 |

两条通道最终都归一到同一套 **scope 校验依赖**,路由层不区分调用者类型。

## 权限 scope

| scope | 含义 | 覆盖的操作 |
|---|---|---|
| `view` | 只读 | 容器列表/详情/日志/stats,栈列表/详情/日志,version,job 详情 |
| `start` | 启动类 | 启动、重启、恢复(unpause)、栈 up、栈重启 |
| `stop` | 停止类 | 停止、暂停(pause)、栈 down |
| `delete` | 删除类 | 删除容器(`?force=`) |
| `admin` | 管理 | API Key 管理(增删改查)、审计日志查看 |

归属原则:让容器"跑起来/继续跑"的操作归 `start`,让容器"停下来/降级"的操作归 `stop`。`admin` 隐含其余全部 scope。

补充规则:

- 存在"仅需认证、不要求具体 scope"的端点(`GET /auth/me`、`PATCH /auth/password`),任意有效主体可访问
- **keys 写操作(create/update/delete)仅接受 JWT 用户主体**——API Key 主体即使有 `admin` scope 也返回 403(防止 key 泄露后自造新 key 持久化后门);`GET /keys` 列表对 admin scope 开放
- **`stacks/{name}/down?volumes=true` 额外要求 `delete` scope**(会永久删除数据卷),默认 `volumes=false` 仅需 `stop`

登录用户的 JWT 携带全部 scope,等价于 `admin`。

## API Key 设计

### 格式与存储

- 明文格式: `dka_` + 43 位随机 base62,示例如 `dka_9xK...`(总长约 47 字符)
- 数据库只存 **SHA-256 哈希**,外加 `key_prefix`(前 8 字符,如 `dka_9xKp`)用于界面辨认
- 明文仅在创建响应中返回**一次**,此后任何接口无法再取出
- 校验方式:请求进来 → 取明文 → 算哈希 → 查表 → 比对(哈希列建唯一索引)

### 生命周期

- 创建:管理员在 Web 界面填名称 + 勾选 scope → 返回明文(仅此一次,界面提示妥善保存)
- 禁用/启用: `enabled` 开关,禁用后立即失效(每次请求实时查库,不做缓存,调用方少无需缓存)
- 删除:硬删除;审计日志**只增不改**,历史审计行保留该 key 的原名称(可读性优先,不影响安全——名称本身不是秘密)
- 记录 `last_used_at`(最近一次成功通过认证的时间,便于清理僵尸 key)

### 密钥与密码安全

- 用户密码: bcrypt(cost 12)
- JWT 签名密钥: 首次启动生成 64 字节随机数,持久化到 `/data/secret.key`(重建容器不失效)
- 所有密钥/哈希/明文密码**永不写入日志**(包括审计日志和应用日志)

## 首次初始化(Setup 模式)

1. 启动时检测 `users` 表:为空 → 进入 setup 模式
2. setup 模式下**仅**放行 `POST /api/v1/auth/setup`(创建管理员用户名+密码)和 `/api/health`,其余全部返回 `503 setup_required`
3. setup 成功后退出 setup 模式
4. 前端检测到 503 自动跳转引导页

JWT 签名密钥在**应用首次启动时**生成并持久化(与 users 表状态无关,见"密钥与密码安全"),setup 成功与否不影响。

不存在任何默认账号/默认密码。

## 防爆破

- `POST /api/v1/auth/login`: 同一 IP 连续失败 5 次 → 锁定 60 秒,期间返回 `429`
- API Key 认证失败(无效 key)不锁定,但记录 warning 日志:含来源 IP 与所尝试 key 的前 8 字符(仅够排障定位,不含完整 key)

## 认证豁免清单(仅此三项)

| 端点 | 理由 |
|---|---|
| `POST /api/v1/auth/login` | 登录本身 |
| `POST /api/v1/auth/setup` | 首次初始化(setup 模式下才可用) |
| `GET /api/health` | 容器 HEALTHCHECK 探针 |

其余一切端点(含 `/api/v1/version`)都必须携带有效凭证。

## HTTP 语义

| 场景 | 状态码 | 错误码 |
|---|---|---|
| 未携带凭证 | 401 | `unauthorized` |
| API Key 被禁用 | 401 | `key_disabled` |
| scope 不足 | 403 | `forbidden` |
| setup 模式访问其他端点 | 503 | `setup_required` |
| 登录锁定中 | 429 | `rate_limited` |
