# DockerAPI 设计文档

- **项目名称**: DockerAPI
- **版本**: v1.0.0(初始版本)
- **日期**: 2026-08-25
- **状态**: 设计定稿,待实现

## 项目定位

DockerAPI 是一个跑在容器里的 Docker 管理平台,提供两条使用通道:

1. **Web 站点**(人类使用): 登录后可查看容器状态与基本信息,暂停/删除/启动容器,通过预配置的 compose 文件启动容器,管理 API Key,查看审计日志。
2. **REST API**(程序使用): 其他 APP 通过带权限 scope 的 API Key 获取容器状态与详情,执行启动/关闭等操作。

核心架构决策: 采用 **DooD(Docker-outside-of-Docker)** 模式——挂载宿主机 `docker.sock`,管理平台操作的所有容器都是宿主机 daemon 的资源。

## 文档索引

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 技术栈、总体架构、核心设计决策 |
| [auth.md](auth.md) | 双通道认证(JWT + API Key)、权限 scope、首次初始化、防爆破 |
| [api.md](api.md) | REST API 完整规范、幂等语义、错误码、长任务模式 |
| [data-model.md](data-model.md) | SQLite 数据模型(users / api_keys / audit_logs / jobs) |
| [logging.md](logging.md) | 日志系统:容器日志、系统日志、审计日志 |
| [frontend.md](frontend.md) | 前端设计(Soybean Admin)、页面清单、实时刷新策略 |
| [deployment.md](deployment.md) | 容器化打包、挂载卷、安全加固、Windows 兼容 |
| [roadmap.md](roadmap.md) | 实施顺序、里程碑、明确不做/延后的事项 |

## 需求摘要

### 人用 Web 站点

- 用户名密码登录
- 容器列表:状态、基本信息,自动刷新
- 容器操作:启动 / 停止 / 重启 / 暂停 / 恢复 / 删除
- 容器详情:inspect 信息(敏感环境变量脱敏)、实时日志、CPU/内存曲线
- Compose 栈:基于 `/opt/stacks` 下预配置的 compose 文件,一键 up/down/重启,查看聚合日志
- API Key 管理:创建(明文仅显示一次)、启停、scope 配置
- 审计日志查看
- 长任务(compose up 等)进度与输出查看

### 程序用 API

- 完整 REST API(见 [api.md](api.md)),FastAPI 自动生成 OpenAPI 交互文档
- API Key 认证,按 scope 授权(查看 / 启动 / 停止 / 删除 / 管理)
- 操作幂等:重复启动已运行的容器返回成功语义
- 统一 JSON 错误格式,HTTP 状态码语义明确

### 明确不做(v1 范围外)

- Web 终端(exec)——攻击面过大,延后;若将来做,仅限管理员并单独审计
- 自由创建容器(任意 HostConfig)——容器创建只能通过 compose 栈,这是一条安全红线
- 镜像管理(列表/删除/手动 pull)——compose up 会自动拉取
- 多主机管理——连接配置预留 `DOCKER_HOST`,架构上留口子但不实现
