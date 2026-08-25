# 数据模型

- 项目: DockerAPI v1.0.0
- 日期: 2026-08-25
- 数据库: SQLite,文件位于 `/data/dockerapi.db`,WAL 模式
- ORM: SQLAlchemy 2.x,启动时自动建表(`create_all`),迁移用 Alembic 留到需要时再引入

共 4 张表:`users` / `api_keys` / `audit_logs` / `jobs`。

## users — 用户

v1 单管理员,表结构为将来多用户留余地。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK 自增 | |
| username | TEXT | UNIQUE NOT NULL | |
| password_hash | TEXT | NOT NULL | bcrypt(cost 12) |
| is_admin | BOOLEAN | NOT NULL DEFAULT 1 | v1 恒为 true |
| created_at | TEXT | NOT NULL | ISO 8601 UTC |

## api_keys — API Key

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK 自增 | |
| name | TEXT | NOT NULL | 展示名,如 "mobile-app" |
| key_hash | TEXT | UNIQUE NOT NULL | 明文的 SHA-256(十六进制) |
| key_prefix | TEXT | NOT NULL | 前 8 字符(`dka_XXXX`),界面辨认用 |
| scopes | TEXT | NOT NULL | JSON 数组字符串,如 `["view","start"]`;`["admin"]` 表示全量 |
| enabled | BOOLEAN | NOT NULL DEFAULT 1 | 禁用后立即失效 |
| created_at | TEXT | NOT NULL | |
| last_used_at | TEXT | NULL | 最近一次认证成功时间 |

明文不落库、不落日志,创建接口仅返回一次。

## audit_logs — 审计日志

只记录**变更类操作**(成功与失败都记),只读操作不记。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK 自增 | |
| ts | TEXT | NOT NULL,索引 | 操作时间 |
| actor_type | TEXT | NOT NULL | `user` / `api_key` / `system` |
| actor_name | TEXT | NOT NULL | 用户名 / key 名称 / `system`(如 JobMgr 自动清理) |
| action | TEXT | NOT NULL,索引 | 见下方动作枚举 |
| target_type | TEXT | NOT NULL | `container` / `stack` / `api_key` / `job` / `auth` |
| target_id | TEXT | NOT NULL | 容器 ID/名称、栈名、key id 等 |
| result | TEXT | NOT NULL | `success` / `error` |
| detail | TEXT | NULL | 失败原因 / 关键参数(如 force=true, t=30);**不含任何密钥明文** |
| ip | TEXT | NULL | 来源 IP |

**动作枚举**:

- 容器: `container.start` `container.stop` `container.restart` `container.pause` `container.unpause` `container.remove`
- 栈: `stack.up` `stack.down` `stack.restart`(**由 JobMgr 在任务终态写入**: done→success,failed→error;actor 随任务传递,detail 携带 job_id)
- Key: `key.create` `key.update` `key.delete`
- 认证: `auth.login`(仅失败记) `auth.setup` `auth.password`(成功与失败均记,2026-08-25 裁决补充)
- 任务: `job.timeout`(系统记录超时任务,同时该栈动作行 result=error)

**保留策略**: 默认 90 天,环境变量 `AUDIT_RETENTION_DAYS` 可调;每日定时清理。审计页面前端提供按 actor/action/target/时间过滤。

## jobs — 长任务

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | TEXT | PK | `j_` + 26 位 ULID(时间有序,`app/util.py` 自实现 Crockford Base32,可作 job_id 直接对外) |
| type | TEXT | NOT NULL | `stack.up` / `stack.down` / `stack.restart` |
| stack | TEXT | NOT NULL | 栈名 |
| status | TEXT | NOT NULL,索引 | `queued` / `running` / `done` / `failed` / `timeout` |
| exit_code | INTEGER | NULL | CLI 退出码 |
| output | TEXT | NOT NULL DEFAULT '' | 滚动缓冲最后 256KB(compose CLI 的合并输出) |
| created_at | TEXT | NOT NULL | |
| started_at | TEXT | NULL | |
| finished_at | TEXT | NULL | |

**保留策略**: 已终结(done/failed/timeout)的任务保留 7 天,启动时 + 每日定时清理。

## 派生关系(非表)

以下数据不落库,运行期派生:

| 数据 | 来源 |
|---|---|
| 容器列表/状态/详情 | daemon 实时查询 |
| 栈清单 | 扫描 `/opt/stacks` 一级子目录 |
| 栈状态 | 按 `com.docker.compose.project` 标签反查容器 |
| stats 序列 | 内存环形缓冲(每容器 1h × 10s) |
| JWT 密钥 | `/data/secret.key` 文件(64 字节随机数) |
