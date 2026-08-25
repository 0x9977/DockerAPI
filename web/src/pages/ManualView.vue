<template>
  <div class="page manual-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">说明书</h2>
        <div class="page-subtitle">Compose 栈配置 · API 调用规范 · 部署与配置 · 开发信息</div>
      </div>
    </div>

    <div class="manual-layout">
      <nav class="manual-toc">
        <a v-for="s in sections" :key="s.id" :href="`#${s.id}`" @click.prevent="scrollTo(s.id)">{{ s.title }}</a>
      </nav>

      <div class="manual-body">
        <!-- ============ 1 快速开始 ============ -->
        <section id="m-quick">
          <h3>1. 快速开始</h3>
          <p>
            DockerAPI 是运行在容器里的 Docker 管理面板：左侧菜单 <b>总览</b> 看 daemon 状态，
            <b>容器</b> 页做日常操作，<b>Compose 栈</b> 管理以目录为单位的服务组，
            <b>API Key</b> 给你的 APP 程序签发调用凭证，<b>审计日志</b> 记录谁在什么时候做了什么。
          </p>
          <ul>
            <li>首次部署: 打开首页会自动跳"初始化"页,创建管理员账号(无默认密码)。</li>
            <li>容器页: 名称前的 <b>详情</b> 按钮进入详情页(日志/统计/端口/环境变量);环境变量中的敏感项(密码/密钥类)已自动打码。</li>
            <li>带 <b>面板自身</b> 标记的容器是本面板自己——禁止一切变更操作(停掉它=整个面板下线,后端同样强制拦截)。</li>
            <li>所有"启动/停止"类操作都是<b>幂等</b>的: 重复点击返回"已在目标状态",不会报错。</li>
          </ul>
        </section>

        <!-- ============ 2 Compose 栈 ============ -->
        <section id="m-stack">
          <h3>2. Compose 栈配置</h3>
          <p>
            面板<b>不提供任意参数创建容器</b>。所有服务以 Compose 栈为单位,放在宿主机
            <code>/opt/stacks</code> 目录下,一个子目录 = 一个栈:
          </p>
          <pre>/opt/stacks/
├── myapp/
│   └── compose.yaml      ← 栈定义
└── redis-stack/
    └── compose.yaml</pre>
          <p>在面板"Compose 栈"页点"启动",等价于宿主机上执行:</p>
          <pre>docker compose -f /opt/stacks/myapp/compose.yaml -p myapp up -d</pre>

          <h4>2.1 命名与文件规则</h4>
          <ul>
            <li>目录名即栈名,必须匹配 <code>^[a-z0-9][a-z0-9_-]{0,63}$</code>(小写开头,仅小写字母/数字/下划线/连字符)。</li>
            <li>compose 文件按优先级识别: <code>compose.yaml</code> &gt; <code>compose.yml</code> &gt; <code>docker-compose.yaml</code> &gt; <code>docker-compose.yml</code>;没有 compose 文件的目录会被忽略。</li>
            <li>栈名同时是 compose 项目名(面板显式传 <code>-p</code>),<code>docker ps</code> 里看到的容器名形如 <code>myapp-xxx-1</code>。</li>
          </ul>

          <h4>2.2 数据卷怎么写(重要)</h4>
          <p>
            bind mount 的路径是<b>宿主机路径</b>(由宿主机 daemon 解析),不是面板容器里的路径。
            面板部署时已把 <code>/opt/stacks</code> 原样挂载,所以推荐两种写法:
          </p>
          <pre># 推荐①: 相对路径——落在栈目录旁,便于备份
services:
  db:
    image: postgres:16
    volumes:
      - ./data:/var/lib/postgresql/data   # → 宿主机 /opt/stacks/myapp/data

# 推荐②: 命名卷——与宿主机路径完全解耦,最省心
volumes:
  pgdata:
services:
  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data</pre>
          <p class="warn">不要在 compose 里写 <code>/mnt/c/...</code> 之类的路径——那是 Windows 开发机上的 daemon 视角,生产 Linux 上会静默创建空目录。</p>

          <h4>2.3 栈操作行为</h4>
          <ul>
            <li><b>启动 up</b>: 后台异步执行(可能含镜像拉取,耗时几分钟),返回任务号,在"任务中心"或弹出抽屉看实时输出。</li>
            <li><b>停止 down</b>: 停止并删除该栈容器;勾选"同时删除数据卷"会<b>永久删除命名卷数据</b>(需要删除权限)。</li>
            <li>同一栈的操作自动排队串行,不会互相打架;全部任务保留 7 天可回溯。</li>
            <li>栈状态含义: running 全部运行 / partial 部分运行 / stopped 全停 / not_created 未创建过。</li>
          </ul>
        </section>

        <!-- ============ 3 API ============ -->
        <section id="m-api">
          <h3>3. API 调用规范</h3>
          <p>给程序调用用 API Key(在"API Key"页创建,明文只显示一次)。基础地址 <code>/api/v1</code>,认证方式:</p>
          <pre>Authorization: Bearer dka_xxxxxxxxxxxxxxxxxxxxxxx</pre>

          <h4>3.1 权限 scope</h4>
          <table>
            <tr><th>scope</th><th>可做的操作</th></tr>
            <tr><td>view</td><td>容器/栈/任务/日志/统计等全部只读</td></tr>
            <tr><td>start</td><td>启动、重启、恢复、栈 up/重启</td></tr>
            <tr><td>stop</td><td>停止、暂停、栈 down</td></tr>
            <tr><td>delete</td><td>删除容器;栈 down 删卷需要它</td></tr>
            <tr><td>admin</td><td>以上全部 + API Key 查询/审计查询</td></tr>
          </table>
          <p class="warn">API Key 管理的增删改只允许管理员在网页上操作——拿 Key 建 Key 是被禁止的。</p>

          <h4>3.2 错误格式(所有非 2xx)</h4>
          <pre>{ "error": { "code": "container_not_found", "message": "No such container: xxx" } }</pre>
          <p>常用 code: <code>unauthorized</code> 未认证 / <code>forbidden</code> 权限不足 / <code>container_not_found</code>·<code>stack_not_found</code> 不存在 / <code>conflict</code> 冲突 / <code>daemon_timeout</code> daemon 无响应 / <code>self_protection</code> 试图操作面板自身容器。</p>

          <h4>3.3 幂等与异步——调用方最重要的两条约定</h4>
          <ul>
            <li><b>容器操作可安全重试</b>: 启动一个已运行的容器返回 <code>200 {"status":"ok","note":"already_in_state"}</code>,不是错误。网络超时后直接重发即可。</li>
            <li><b>栈操作是异步的</b>: up/down/restart 立即返回 <code>202 {"job_id":"j_xxx"}</code>,轮询 <code>GET /api/v1/jobs/{job_id}</code> 看 status(done/failed/timeout)和 output。</li>
          </ul>

          <h4>3.4 常用端点速查</h4>
          <pre>GET  /api/v1/containers                 容器列表(含 is_self 标记和最近30点CPU/内存)
GET  /api/v1/containers/{id}           详情(敏感环境变量已打码)
POST /api/v1/containers/{id}/start|stop|restart|pause|unpause
DELETE /api/v1/containers/{id}?force=false
GET  /api/v1/containers/{id}/logs?tail=200        最近日志
GET  /api/v1/containers/{id}/logs/stream?tail=200 实时日志(SSE)
GET  /api/v1/containers/{id}/stats                CPU/内存采样序列
GET  /api/v1/stacks                     栈列表
POST /api/v1/stacks/{name}/up           → 202 + job_id
POST /api/v1/stacks/{name}/down?volumes=false     → 202 + job_id
GET  /api/v1/jobs/{job_id}              任务状态与输出
GET  /api/v1/version                    面板与 daemon 版本摘要</pre>

          <h4>3.5 curl 示例</h4>
          <pre>KEY="dka_你的key"
# 查容器
curl -H "Authorization: Bearer $KEY" http://面板地址/api/v1/containers
# 启动容器(重复调用安全)
curl -X POST -H "Authorization: Bearer $KEY" http://面板地址/api/v1/containers/myapp-web-1/start
# 启动栈(异步)
curl -X POST -H "Authorization: Bearer $KEY" http://面板地址/api/v1/stacks/myapp/up
# → {"job_id":"j_01HZ..."}  然后轮询:
curl -H "Authorization: Bearer $KEY" http://面板地址/api/v1/jobs/j_01HZ...</pre>

          <h4>3.6 SSE 实时流约定</h4>
          <ul>
            <li>日志流每帧 <code>data: {"stream":"stdout","line":"..."}</code>;连接建立时回放最近 <code>tail</code> 行(默认 200),<b>客户端重连后应清空本地缓冲再收</b>,否则会重复。</li>
            <li>任务流每帧 <code>data: {"chunk":"..."}</code>,结束发 <code>event: end</code>。</li>
            <li>收到 <code>event: error</code> 表示业务性错误(容器不存在/订阅超限),<b>不要重连</b>。浏览器端请用 fetch 流式方案(如 @microsoft/fetch-event-source)带 Authorization 头,原生 EventSource 带不了头。</li>
          </ul>
        </section>

        <!-- ============ 4 部署 ============ -->
        <section id="m-deploy">
          <h3>4. 部署与配置</h3>
          <h4>4.1 三个挂载点</h4>
          <table>
            <tr><th>挂载</th><th>作用</th><th>丢了会怎样</th></tr>
            <tr><td>/var/run/docker.sock</td><td>连接宿主 Docker</td><td>所有 Docker 操作失败</td></tr>
            <tr><td>/opt/stacks</td><td>compose 栈文件(路径内外一致)</td><td>栈列表为空</td></tr>
            <tr><td>/data</td><td>数据库/密钥/日志(建议命名卷)</td><td>容器重建=账号和 Key 全丢</td></tr>
          </table>
          <h4>4.2 常用环境变量</h4>
          <pre>DATA_DIR=/data              持久化目录(默认)
STACKS_DIR=/opt/stacks      栈扫描目录(默认)
DOCKER_HOST=unix:///var/run/docker.sock   可改 tcp:// 或 ssh://
DOCKER_TIMEOUT=30           单次 daemon 调用超时(秒)
COMPOSE_JOB_TIMEOUT=1800    compose 任务超时(秒)
AUDIT_RETENTION_DAYS=90     审计保留天数
LOG_LEVEL=INFO              日志级别</pre>
          <h4>4.3 升级与备份</h4>
          <ul>
            <li>备份 = 备 <code>/opt/stacks</code> + <code>/data</code> 两处(SQLite 可直接停面板后拷贝)。</li>
            <li>升级: 换新镜像 <code>docker compose up -d</code> 重建即可,<code>/data</code> 卷不动,账号/Key/审计全保留。</li>
            <li>非 root 运行需要 <code>--group-add &lt;宿主机 docker 组 GID&gt;</code>(每台机器不同,<code>getent group docker</code> 查)。</li>
          </ul>
        </section>

        <!-- ============ 5 开发信息 ============ -->
        <section id="m-dev">
          <h3>5. 开发信息</h3>
          <ul>
            <li>技术栈: 后端 FastAPI + docker-py + SQLite;前端 Vue 3 + Naive UI(暗色);单容器交付(ubuntu:24.04 基础镜像)。</li>
            <li>交互文档: <code>/docs</code>(Swagger UI,内网可直接打开)。</li>
            <li>设计文档是单一事实来源,在仓库 <code>docs/</code> 目录(9 篇);改设计先改文档再改代码。</li>
            <li>本地开发: <code>cd backend && uvicorn app.main:app --reload --port 8000</code>;前端 <code>cd web && npm run dev</code>(代理 /api)。</li>
            <li>测试: <code>pytest</code>(120+ 单测);真机集成 <code>python tests/integration_vm.py http://127.0.0.1:8000</code>。</li>
            <li>已知设计要点: 不做容器状态内存表(daemon 是唯一状态源,直查);docker-py 全部经线程池桥接;面板自身容器禁止变更(自保护)。</li>
          </ul>
        </section>

        <!-- ============ 6 安全 ============ -->
        <section id="m-sec">
          <h3>6. 安全说明</h3>
          <ul>
            <li>本面板面向<b>内网部署</b>:端口只绑内网,未启用 HTTPS;如需暴露到更大网络,请前置 Caddy/Nginx 加 TLS。</li>
            <li>API Key 明文只在创建时显示一次,库里只存哈希;日志自动打码(dka_ 前缀/Bearer/密码字段)。</li>
            <li>登录防爆破: 同 IP 连续错 5 次锁 60 秒。</li>
            <li>所有变更操作(含失败的)都进审计日志,保留 90 天。</li>
            <li>docker.sock ≈ 宿主机 root 权限——请像保管服务器密码一样保管面板账号与 API Key。</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';

const sections = [
  { id: 'm-quick', title: '1. 快速开始' },
  { id: 'm-stack', title: '2. Compose 栈配置' },
  { id: 'm-api', title: '3. API 调用规范' },
  { id: 'm-deploy', title: '4. 部署与配置' },
  { id: 'm-dev', title: '5. 开发信息' },
  { id: 'm-sec', title: '6. 安全说明' },
];

const active = ref(sections[0].id);

function scrollTo(id: string): void {
  active.value = id;
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
</script>

<style scoped>
.manual-layout {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}
.manual-toc {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 150px;
  font-size: 13px;
}
.manual-toc a {
  color: var(--n-text-color-3, #9aa0b0);
  text-decoration: none;
  padding: 4px 10px;
  border-left: 2px solid transparent;
}
.manual-toc a:hover {
  color: var(--n-text-color-1, #e6e8ef);
}
.manual-body {
  flex: 1;
  min-width: 0;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 10px;
  padding: 8px 28px 28px;
}
.manual-body section {
  padding-top: 22px;
  scroll-margin-top: 10px;
}
.manual-body h3 {
  font-size: 17px;
  margin: 6px 0 10px;
}
.manual-body h4 {
  font-size: 14px;
  margin: 18px 0 6px;
  color: #8f9bff;
}
.manual-body p,
.manual-body li {
  font-size: 13.5px;
  line-height: 1.75;
  color: rgba(230, 232, 239, 0.88);
}
.manual-body pre {
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 12.5px;
  line-height: 1.6;
  overflow-x: auto;
  font-family: ui-monospace, Consolas, monospace;
}
.manual-body code {
  background: rgba(143, 155, 255, 0.12);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 12.5px;
}
.manual-body pre code {
  background: none;
  padding: 0;
}
.manual-body table {
  border-collapse: collapse;
  font-size: 13px;
  margin: 8px 0;
  width: 100%;
}
.manual-body th,
.manual-body td {
  border: 1px solid rgba(255, 255, 255, 0.1);
  padding: 6px 10px;
  text-align: left;
}
.manual-body th {
  color: rgba(230, 232, 239, 0.95);
  background: rgba(255, 255, 255, 0.04);
}
.manual-body .warn {
  color: #e6a23c;
}
</style>
