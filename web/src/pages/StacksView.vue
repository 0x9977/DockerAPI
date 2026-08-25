<script setup lang="ts">
import { computed, ref } from 'vue';
import { RouterLink } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDivider,
  NEmpty,
  NModal,
  NSpin,
  NTag,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import JobDrawer from '../components/JobDrawer.vue';
import LogViewer from '../components/LogViewer.vue';
import StateBadge from '../components/StateBadge.vue';
import { usePoll } from '../composables/usePoll';
import { message } from '../utils/feedback';
import { fmtRelative } from '../utils/format';
import type { LogLine } from '../types';

interface StackItem {
  name: string;
  status: string;
  container_count: number;
  running_count: number;
}

interface StackContainer {
  id: string;
  name: string;
  image: string;
  state: string;
  created?: string;
}

interface StackDetail {
  name: string;
  status: string;
  compose_yaml: string;
  containers?: StackContainer[];
}

type TagType = 'success' | 'warning' | 'error' | 'info' | 'default';

const STACK_STATUS: Record<string, { type: TagType; label: string }> = {
  running: { type: 'success', label: '运行中' },
  partial: { type: 'warning', label: '部分运行' },
  stopped: { type: 'default', label: '已停止' },
  not_created: { type: 'info', label: '未创建' },
  unknown: { type: 'default', label: '未知' },
};

function statusMeta(status: string): { type: TagType; label: string } {
  return STACK_STATUS[status] ?? { type: 'default', label: status || '未知' };
}

/* ---------------- 栈列表(5s 轮询) ---------------- */

const stacks = ref<StackItem[]>([]);
const errorMsg = ref('');
const refreshing = ref(false);
const firstLoading = ref(true);
const busy = ref<Record<string, boolean>>({});

async function load(): Promise<void> {
  try {
    const list = await api<StackItem[]>('/stacks');
    stacks.value = Array.isArray(list) ? list : [];
    errorMsg.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) errorMsg.value = err.message;
  } finally {
    firstLoading.value = false;
  }
}

const { refresh } = usePoll(load, 5000);

async function refreshNow(): Promise<void> {
  refreshing.value = true;
  await refresh();
  refreshing.value = false;
}

const runningStacks = computed(() => stacks.value.filter((s) => s.status === 'running').length);

/* ---------------- 栈操作(202 → 任务抽屉) ---------------- */

const drawerShow = ref(false);
const drawerJobId = ref<string | null>(null);

function openJob(jobId: string): void {
  drawerJobId.value = jobId;
  drawerShow.value = true;
}

async function submitAction(name: string, action: 'up' | 'restart'): Promise<void> {
  const key = `${name}:${action}`;
  if (busy.value[key]) return;
  busy.value = { ...busy.value, [key]: true };
  try {
    const r = await api<{ job_id: string }>(`/stacks/${encodeURIComponent(name)}/${action}`, {
      method: 'POST',
    });
    message.success(`已提交${actionLabel(action)}任务`);
    openJob(r.job_id);
    await refresh();
  } catch (e) {
    message.error((e as ApiError).message);
  } finally {
    const next = { ...busy.value };
    delete next[key];
    busy.value = next;
  }
}

function actionLabel(action: 'up' | 'restart'): string {
  return action === 'up' ? '启动' : '重启';
}

/* 停止确认(可选连带删卷) */

const downShow = ref(false);
const downBusy = ref(false);
const downVolumes = ref(false);
const downItem = ref<StackItem | null>(null);

function openDown(item: StackItem): void {
  downItem.value = item;
  downVolumes.value = false;
  downShow.value = true;
}

async function confirmDown(): Promise<boolean> {
  const item = downItem.value;
  if (!item) return true;
  downBusy.value = true;
  try {
    const r = await api<{ job_id: string }>(`/stacks/${encodeURIComponent(item.name)}/down`, {
      method: 'POST',
      query: { volumes: downVolumes.value },
    });
    message.success('已提交停止任务');
    openJob(r.job_id);
    downShow.value = false;
    await refresh();
    return true;
  } catch (e) {
    message.error((e as ApiError).message);
    return false;
  } finally {
    downBusy.value = false;
  }
}

/* ---------------- 卡片展开详情 ---------------- */

const expanded = ref<Record<string, boolean>>({});
const details = ref<Record<string, StackDetail | null>>({});
const detailLoading = ref<Record<string, boolean>>({});
const detailError = ref<Record<string, string>>({});

function toggleExpand(name: string): void {
  const next = !expanded.value[name];
  expanded.value = { ...expanded.value, [name]: next };
  if (next) void loadDetail(name);
}

async function loadDetail(name: string, force = false): Promise<void> {
  if (!force && details.value[name]) return;
  detailLoading.value = { ...detailLoading.value, [name]: true };
  try {
    const d = await api<StackDetail>(`/stacks/${encodeURIComponent(name)}`);
    details.value = { ...details.value, [name]: d };
    detailError.value = { ...detailError.value, [name]: '' };
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) {
      detailError.value = { ...detailError.value, [name]: err.message };
    }
  } finally {
    detailLoading.value = { ...detailLoading.value, [name]: false };
  }
}

/* ---------------- 栈日志弹窗 ---------------- */

const logsShow = ref(false);
const logsName = ref('');
const logsLoading = ref(false);
const logsError = ref('');
const logsLines = ref<LogLine[]>([]);

async function openLogs(name: string): Promise<void> {
  logsName.value = name;
  logsShow.value = true;
  logsLoading.value = true;
  logsError.value = '';
  logsLines.value = [];
  try {
    const r = await api<{ lines?: Array<{ stream?: string; line?: string }> }>(
      `/stacks/${encodeURIComponent(name)}/logs`,
      { query: { tail: 200 } }
    );
    logsLines.value = (r.lines ?? []).map((l) => ({
      stream: l.stream ?? 'stdout',
      text: l.line ?? '',
    }));
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) logsError.value = err.message;
  } finally {
    logsLoading.value = false;
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">Compose 栈</h2>
        <div class="page-subtitle">
          {{ stacks.length }} 个栈 · {{ runningStacks }} 个运行中 · 每 5 秒自动刷新
        </div>
      </div>
      <div class="page-actions">
        <n-button size="small" :loading="refreshing" @click="refreshNow">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">{{ errorMsg }}</n-alert>

    <n-empty
      v-if="!firstLoading && stacks.length === 0 && !errorMsg"
      size="large"
      description="未发现 Compose 栈"
      style="padding: 48px 0"
    >
      <template #extra>
        <span class="dim">在宿主机 /opt/stacks 下放置包含 compose 文件的目录即可被扫描</span>
      </template>
    </n-empty>

    <div class="stack-grid">
      <n-card v-for="s in stacks" :key="s.name" size="small" class="stack-card">
        <template #header>
          <span class="mono stack-name">{{ s.name }}</span>
        </template>
        <template #header-extra>
          <n-tag size="small" :bordered="false" round :type="statusMeta(s.status).type">
            {{ statusMeta(s.status).label }}
          </n-tag>
        </template>

        <div class="stack-meta">
          <span class="dim">容器</span>
          <span class="stack-count mono">{{ s.running_count }}/{{ s.container_count }}</span>
          <span class="dim">运行中</span>
        </div>

        <div class="stack-actions">
          <n-button
            size="tiny"
            quaternary
            type="success"
            :loading="!!busy[`${s.name}:up`]"
            @click="submitAction(s.name, 'up')"
          >
            启动
          </n-button>
          <n-button
            size="tiny"
            quaternary
            type="warning"
            :loading="!!busy[`${s.name}:down`]"
            @click="openDown(s)"
          >
            停止
          </n-button>
          <n-button
            size="tiny"
            quaternary
            :loading="!!busy[`${s.name}:restart`]"
            @click="submitAction(s.name, 'restart')"
          >
            重启
          </n-button>
          <span class="action-gap"></span>
          <n-button size="tiny" quaternary @click="openLogs(s.name)">日志</n-button>
          <n-button size="tiny" quaternary @click="toggleExpand(s.name)">
            {{ expanded[s.name] ? '收起' : '详情' }}
          </n-button>
        </div>

        <template v-if="expanded[s.name]">
          <n-divider style="margin: 12px 0 10px" />
          <n-alert
            v-if="detailError[s.name]"
            type="error"
            style="margin-bottom: 10px"
          >
            {{ detailError[s.name] }}
          </n-alert>
          <n-spin :show="!!detailLoading[s.name]" size="small">
            <template v-if="details[s.name]">
              <div class="detail-head">
                <span class="detail-title">compose.yml</span>
                <n-button
                  size="tiny"
                  quaternary
                  @click="loadDetail(s.name, true)"
                >
                  刷新
                </n-button>
              </div>
              <div class="mono-box compose-box">{{ details[s.name]?.compose_yaml || '(空文件)' }}</div>

              <div class="detail-head" style="margin-top: 12px">
                <span class="detail-title">
                  关联容器({{ details[s.name]?.containers?.length ?? 0 }})
                </span>
              </div>
              <div v-if="!(details[s.name]?.containers ?? []).length" class="dim detail-empty">
                该栈尚无容器(未创建或已销毁)
              </div>
              <div v-else class="stack-containers">
                <div
                  v-for="c in details[s.name]?.containers ?? []"
                  :key="c.id"
                  class="stack-container"
                >
                  <StateBadge :state="c.state" />
                  <RouterLink
                    :to="`/containers/${encodeURIComponent(c.id)}`"
                    class="container-link mono"
                  >
                    {{ c.name || c.id.slice(0, 12) }}
                  </RouterLink>
                  <span class="dim mono container-image">{{ c.image }}</span>
                  <span class="dim container-created" :title="c.created ?? ''">
                    {{ fmtRelative(c.created) }}
                  </span>
                </div>
              </div>
            </template>
            <div v-else-if="!detailError[s.name]" class="dim detail-empty">加载中…</div>
          </n-spin>
        </template>
      </n-card>
    </div>

    <!-- 停止栈确认 -->
    <n-modal
      v-model:show="downShow"
      preset="dialog"
      type="warning"
      title="停止栈"
      :loading="downBusy"
      positive-text="停止"
      negative-text="取消"
      @positive-click="confirmDown"
      @negative-click="downShow = false"
    >
      <p style="margin: 0 0 10px">
        确定停止栈
        <code class="mono">{{ downItem?.name }}</code>
        吗?将执行 <code class="mono">docker compose down</code>(异步任务)。
      </p>
      <n-checkbox v-model:checked="downVolumes">
        同时删除数据卷(需 delete 权限,命名卷数据将被永久删除)
      </n-checkbox>
    </n-modal>

    <!-- 栈日志 -->
    <n-modal v-model:show="logsShow" preset="card" title="栈日志" class="logs-modal">
      <template #header>
        <span>栈日志 <span class="mono dim">{{ logsName }}</span></span>
      </template>
      <n-alert v-if="logsError" type="error" style="margin-bottom: 10px">{{ logsError }}</n-alert>
      <n-spin :show="logsLoading">
        <LogViewer :lines="logsLines" height="480px" placeholder="暂无日志输出" />
      </n-spin>
      <div class="dim logs-hint">最近 200 行,聚合自该栈全部容器;实时日志请前往各容器详情页</div>
    </n-modal>

    <!-- 任务抽屉 -->
    <JobDrawer v-model:show="drawerShow" :job-id="drawerJobId" />
  </div>
</template>

<style scoped>
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stack-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 12px;
  align-items: start;
}

.stack-card {
  min-width: 0;
}

.stack-name {
  font-weight: 600;
  font-size: 14px;
}

.stack-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 13px;
}

.stack-count {
  font-size: 16px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.85);
}

.stack-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
}

.action-gap {
  width: 10px;
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.detail-title {
  font-size: 12px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.55);
}

.compose-box {
  max-height: 320px;
}

.detail-empty {
  padding: 12px 0;
  text-align: center;
  font-size: 12px;
}

.stack-containers {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.stack-container {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 12px;
}

.container-link {
  color: #8f9bff;
  white-space: nowrap;
}

.container-link:hover {
  text-decoration: underline;
}

.container-image {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.container-created {
  white-space: nowrap;
  flex-shrink: 0;
}

.logs-modal {
  width: 860px;
  max-width: calc(100vw - 48px);
}

.logs-hint {
  font-size: 12px;
  margin-top: 8px;
}
</style>
