# DockerAPI

[![docker-image](https://github.com/0x9977/DockerAPI/actions/workflows/docker-image.yml/badge.svg)](../../actions/workflows/docker-image.yml)

跑在容器里的 Docker 管理平台:挂载宿主机 `docker.sock`(DooD 模式),给**人**一个暗色 Web 面板,给**程序**一套带权限的 REST API。

容器只能通过 `/opt/stacks` 下的 compose 栈创建——不提供任意参数创建、不提供 exec 终端,从根上消灭 privileged / 挂宿主敏感路径这类危险操作。

## 功能

| 面向 | 能力 |
|---|---|
| 人类(Web) | 总览仪表盘 · 容器列表/详情(操作幂等)· 实时日志(SSE) · CPU/内存曲线 · Compose 栈一键 up/down(实时输出) · API Key 管理 · 审计日志 · 内置说明书 |
| 程序(API) | REST `/api/v1` + Swagger 交互文档(`/docs`) · API Key 五级 scope(查看/启动/停止/删除/管理) · 操作幂等可安全重试 · 长任务 202+job 模式 · SSE 日志流 |
| 安全 | 无默认密码(首启强制初始化) · Key 只存哈希、明文仅显示一次 · 日志脱敏 · 登录防爆破 · 全量变更审计 · **面板自身容器操作保护** |

## Docker 部署(推荐)

镜像自动构建:每次推送 main 更新 `ghcr.io/0x9977/dockerapi:latest`,发版打 `v*` 标签。

### 方式一: docker compose(推荐)

```bash
# 1. 准备栈目录(以后要部署的服务都放这里,一个子目录一个栈)
mkdir -p /opt/stacks

# 2. 取部署配置
curl -o docker-compose.yml https://raw.githubusercontent.com/0x9977/DockerAPI/main/docker-compose.yml

# 3. 填宿主机 docker 组 GID(每台机器不同)
sed -i "s/\"999\"/\"$(getent group docker | cut -d: -f3)\"/" docker-compose.yml

# 4. 启动
docker compose up -d
```

打开 `http://<主机IP>:8000`,首次访问会引导你创建管理员账号(无默认密码)。

### 方式二: docker run

```bash
mkdir -p /opt/stacks
docker run -d --name dockerapi \
  --restart unless-stopped \
  -p 8000:8000 \
  --group-add $(getent group docker | cut -d: -f3) \
  --read-only --tmpfs /tmp \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/stacks:/opt/stacks \
  -v dockerapi-data:/data \
  -e TZ=Asia/Shanghai \
  ghcr.io/0x9977/dockerapi:latest
```

### 方式三: 从源码构建

```bash
git clone https://github.com/0x9977/DockerAPI.git
cd DockerAPI
docker compose up -d --build   # 根目录 compose 里放开 build 段即可
```

### 三个挂载点(缺一不可的原理)

| 挂载 | 作用 | 缺失后果 |
|---|---|---|
| `/var/run/docker.sock` | 连接宿主 Docker daemon | 所有 Docker 操作失败 |
| `/opt/stacks` | compose 栈文件(路径内外一致) | 栈列表为空 |
| `/data`(命名卷) | 账号/API Key/审计持久化 | 容器重建即丢失全部配置 |

> `--group-add` 是非 root 运行的 socket 授权;Windows Docker Desktop 开发环境改挂 `//./pipe/docker_engine:/var/run/docker.sock`,无需 group_add。

### 升级与备份

```bash
docker compose pull && docker compose up -d   # 升级:/data 卷不动,账号与 Key 全保留
```

备份 = 备 `/opt/stacks` + `/data` 两处。

## 快速上手

1. **部署服务**:在宿主机 `/opt/stacks/<栈名>/compose.yaml` 放 compose 文件(命名规则 `^[a-z0-9][a-z0-9_-]{0,63}$`),面板"Compose 栈"页一键启动。数据卷推荐命名卷或栈内相对路径——bind mount 写的是**宿主机路径**。
2. **给 APP 签发 Key**:面板"API Key"页创建,勾选所需 scope,明文只显示一次。
3. **调用**:

```bash
KEY="dka_你的key"
# 查容器(可安全重试的幂等操作)
curl -H "Authorization: Bearer $KEY" http://主机:8000/api/v1/containers
# 启动 compose 栈(异步,返回 job_id 后轮询 /jobs/{id})
curl -X POST -H "Authorization: Bearer $KEY" http://主机:8000/api/v1/stacks/myapp/up
```

完整规范见面板内置"说明书"页或 [docs/api.md](docs/api.md);交互文档在 `/docs`。

## 安全说明

- 面向**内网**设计(端口只绑内网,未启用 HTTPS);要暴露更大网络请前置 Caddy/Nginx 加 TLS
- `docker.sock` ≈ 宿主机 root 权限,请像服务器密码一样保管面板账号与 API Key
- 所有变更操作(含失败的)入审计日志,默认保留 90 天

## 文档与开发

- 设计文档(单一事实来源): [docs/](docs/) — 架构 / 认证 / API / 数据模型 / 日志 / 前端 / 部署 / 路线图
- 技术栈: FastAPI + docker-py + SQLite ｜ Vue3 + Naive UI(暗色) ｜ 单容器交付(ubuntu:24.04)
- 本地开发: `cd backend && uvicorn app.main:app --reload` + `cd web && npm run dev`;测试 `pytest`(120+ 单测)与 `backend/tests/integration_vm.py`(真机集成)
- 协作规范: [AGENTS.md](AGENTS.md)

## License

[MIT](LICENSE)
