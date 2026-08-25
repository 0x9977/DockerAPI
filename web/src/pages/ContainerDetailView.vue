<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  NAlert,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import LogViewer from '../components/LogViewer.vue';
import Sparkline from '../components/Sparkline.vue';
import StateBadge from '../components/StateBadge.vue';
import { usePoll } from '../composables/usePoll';
import { message } from '../utils/feedback';
import { fmtDateTime, fmtMB } from '../utils/format';
import { createSse, sseUrl, type SseState } from '../utils/sse';
import type { LogLine, StatPoint } from '../types';

const route = useRoute();
const cid = computed(() => String(route.params.id ?? ''));

/* ================= 基本信息 ================= */

const detail = ref<Record<string, unknown> | null>(null);
const detailLoading = ref(true);
const detailError = ref('');

function pick(source: Record<string, unknown> | null, ...keys: string[]): unknown {
  for (const key of keys) {
    const value = key.split('.').reduce<unknown>((o, k) => {
      if (o !== null && o !== undefined && typeof o === 'object') {
        return (o as Record<string, unknown>)[k];
      }
      return undefined;
    }, source);
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

const info = computed(() => {
  const d = detail.value;
  return {
    id: pick(d, 'id', 'Id'),
    name: String(pick(d, 'name', 'Name') ?? '').replace(/^\//, '') || '—',
    image: pick(d, 'image', 'config.image', 'Config.Image'),
    state: pick(d, 'state', 'state.status', 'State.Status'),
    created: pick(d, 'created', 'Created'),
    ports: pick(d, 'ports', 'network_settings.ports', 'NetworkSettings.Ports', 'host_config.port_bindings', 'HostConfig.PortBindings'),
    mounts: pick(d, 'mounts', 'Mounts'),
    restartPolicy: pick(d, 'restart_policy', 'host_config.restart_policy', 'HostConfig.RestartPolicy'),
    env: pick(d, 'env', 'config.env', 'Config.Env'),
    labels: pick(d, 'labels', 'config.labels', 'Config.Labels'),
  };
});

const portList = computed<string[]>(() => {
  const p = info.value.ports;
  if (!p || typeof p !== 'object' || Array.isArray(p)) return [];
  const out: string[] = [];
  for (const [cport, bindings] of Object.entries(p as Record<string, unknown>)) {
    if (Array.isArray(bindings) && bindings.length > 0) {
      for (const b of bindings as Array<Record<string, unknown>>) {
        const hostIp = typeof b.HostIp === 'string' && b.HostIp !== '' && b.HostIp !== '0.0.0.0' ? `${b.HostIp}:` : '';
        const hostPort = b.HostPort ?? '?';
        out.push(`${hostIp}${hostPort} → ${cport}`);
      }
    } else {
      out.push(`${cport}(未发布)`);
    }
  }
  return out;
});

const mountList = computed<string[]>(() => {
  const m = info.value.mounts;
  if (!Array.isArray(m)) return [];
  return m.map((e) => {
    const ent = e as Record<string, unknown>;
    const src = String(ent.Source ?? ent.source ?? '') || '(匿名卷)';
    const dst = String(ent.Destination ?? ent.destination ?? '');
    const mode = String(ent.Mode ?? ent.mode ?? ent.Type ?? ent.type ?? '');
    return `${src} → ${dst}${mode ? ` (${mode})` : ''}`;
  });
});

const restartPolicyText = computed(() => {
  const rp = info.value.restartPolicy;
  if (rp === null || rp === undefined) return '—';
  if (typeof rp === 'string') return rp;
  if (typeof rp === 'object') {
    const o = rp as Record<string, unknown>;
    const name = o.Name ?? o.name;
    if (name) return String(name);
  }
  return '—';
});

const envLines = computed<string[]>(() => {
  const env = info.value.env;
  return Array.isArray(env) ? env.map((x) => String(x)) : [];
});

const labelLines = computed<string[]>(() => {
  const labels = info.value.labels;
  if (labels && typeof labels === 'object' && !Array.isArray(labels)) {
    return Object.entries(labels as Record<string, unknown>).map(([k, v]) => `${k}=${v}`);
  }
  return [];
});

async function loadDetail(): Promise<void> {
  detailLoading.value = true;
  detailError.value = '';
  try {
    detail.value = await api<Record<string, unknown>>(`/containers/${encodeURIComponent(cid.value)}`);
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) detailError.value = err.message;
  } finally {
    detailLoading.value = false;
  }
}

void loadDetail();

/* ================= 日志 ================= */

const logLines = ref<LogLine[]>([]);
const logsLoading = ref(false);
const logsError = ref('');
const live = ref(false);
const liveState = ref<SseState>('closed');
let sseHandle: { stop: () => void } | null = null;

async function loadLogs(): Promise<void> {
  logsLoading.value = true;
  logsError.value = '';
  try {
    const r = await api<{ lines?: Array<{ stream?: string; line?: string; ts?: string | null }> }>(
      `/containers/${encodeURIComponent(cid.value)}/logs`,
      { query: { tail: 500 } }
    );
    logLines.value = (r.lines ?? []).map((l) => ({
      stream: l.stream ?? 'stdout',
      text: l.line ?? '',
      ts: l.ts ?? null,
    }));
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) logsError.value = err.message;
  } finally {
    logsLoading.value = false;
  }
}

void loadLogs();

function pushLogLine(stream: string, text: string, ts?: string | null): void {
  logLines.value.push({ stream, text, ts });
  if (logLines.value.length > 5000) {
    logLines.value.splice(0, logLines.value.length - 5000);
  }
}

function startLive(): void {
  stopLive();
  liveState.value = 'connecting';
  // tail 限制回放量;每次(重)连成功(open)时清空本地缓冲——服务端会回放最近
  // tail 行,本地不清就会叠加重复(审计 C1)
  sseHandle = createSse(sseUrl(`/containers/${encodeURIComponent(cid.value)}/logs/stream?tail=500`), {
    onMessage(msg) {
      try {
        const d = JSON.parse(msg.data) as { stream?: string; line?: string; ts?: string | null };
        pushLogLine(d.stream ?? 'stdout', d.line ?? '', d.ts ?? null);
      } catch {
        /* 忽略无法解析的帧 */
      }
    },
    onEnd() {
      live.value = false;
      message.info('日志流已结束(容器停止或被移除)');
    },
    onErrorEvent(data) {
      live.value = false;
      let hint = '日志流错误';
      try {
        const d = JSON.parse(data ?? '{}') as { message?: string; code?: string };
        if (d.code === 'too_many_streams') hint = '订阅数已达上限,请稍后再试';
        else if (d.code === 'container_not_found') hint = '容器不存在';
        else if (d.message) hint = d.message;
      } catch {
        /* 保底文案 */
      }
      message.error(hint);
    },
    onStateChange(s) {
      if (s === 'open') logLines.value = []; // (重)连成功,服务端将回放 tail 行
      liveState.value = s;
    },
  });
}

function stopLive(): void {
  sseHandle?.stop();
  sseHandle = null;
  liveState.value = 'closed';
}

watch(live, (on) => {
  if (on) startLive();
  else stopLive();
});

onUnmounted(stopLive);

const liveStateText = computed(() => {
  switch (liveState.value) {
    case 'connecting':
      return '连接中';
    case 'open':
      return '实时中';
    case 'retrying':
      return '重连中';
    default:
      return '已停止';
  }
});

const liveTagType = computed(() => {
  switch (liveState.value) {
    case 'open':
      return 'success';
    case 'retrying':
    case 'connecting':
      return 'warning';
    default:
      return 'default';
  }
});

/* ================= Stats ================= */

const stats = ref<StatPoint[]>([]);
const statsError = ref('');

async function loadStats(): Promise<void> {
  try {
    const r = await api<StatPoint[]>(`/containers/${encodeURIComponent(cid.value)}/stats`);
    stats.value = Array.isArray(r) ? r : [];
    statsError.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) statsError.value = err.message;
  }
}

usePoll(loadStats, 5000);

function downsample<T>(arr: T[], max: number): T[] {
  if (arr.length <= max) return arr;
  const step = Math.ceil(arr.length / max);
  const out = arr.filter((_, i) => i % step === 0);
  if (out[out.length - 1] !== arr[arr.length - 1]) out.push(arr[arr.length - 1]);
  return out;
}

const cpuSeries = computed(() => downsample(stats.value.map((p) => p.cpu_percent ?? 0), 240));
const memSeries = computed(() => downsample(stats.value.map((p) => p.mem_mb ?? 0), 240));
const lastStat = computed<StatPoint | null>(() =>
  stats.value.length ? stats.value[stats.value.length - 1] : null
);
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title detail-title">
          <span class="mono">{{ info.name }}</span>
          <StateBadge :state="info.state ? String(info.state) : null" />
        </h2>
        <div class="page-subtitle mono">{{ info.image ?? '—' }}</div>
      </div>
    </div>

    <n-alert v-if="detailError" type="error" title="加载容器详情失败" style="margin-bottom: 14px">
      {{ detailError }}
    </n-alert>

    <n-spin :show="detailLoading">
      <n-card size="small" title="基本信息" style="margin-bottom: 14px">
        <n-descriptions bordered :column="2" label-placement="left" size="small">
          <n-descriptions-item label="容器 ID">
            <span class="mono id-text">{{ info.id ?? '—' }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="创建时间">{{ fmtDateTime(info.created ? String(info.created) : null) }}</n-descriptions-item>
          <n-descriptions-item label="状态">
            <StateBadge :state="info.state ? String(info.state) : null" />
          </n-descriptions-item>
          <n-descriptions-item label="重启策略">{{ restartPolicyText }}</n-descriptions-item>
          <n-descriptions-item label="端口" :span="2">
            <span v-if="portList.length === 0" class="dim">—</span>
            <div v-else class="port-list">
              <div v-for="(p, i) in portList" :key="i" class="mono">{{ p }}</div>
            </div>
          </n-descriptions-item>
          <n-descriptions-item label="挂载" :span="2">
            <span v-if="mountList.length === 0" class="dim">—</span>
            <div v-else class="port-list">
              <div v-for="(m, i) in mountList" :key="i" class="mono">{{ m }}</div>
            </div>
          </n-descriptions-item>
        </n-descriptions>
      </n-card>
    </n-spin>

    <n-card size="small" title="环境变量" style="margin-bottom: 14px">
      <template #header-extra>
        <span class="dim extra-hint">敏感变量已由服务端脱敏</span>
      </template>
      <div v-if="envLines.length === 0" class="dim">无</div>
      <div v-else class="mono-box">{{ envLines.join('\n') }}</div>
    </n-card>

    <n-card size="small" title="标签" style="margin-bottom: 14px">
      <div v-if="labelLines.length === 0" class="dim">无</div>
      <div v-else class="mono-box">{{ labelLines.join('\n') }}</div>
    </n-card>

    <n-tabs type="line" default-value="logs" animated>
      <n-tab-pane name="logs" tab="日志">
        <div class="logs-toolbar">
          <div class="logs-toolbar-left">
            <span class="dim">实时</span>
            <n-switch v-model:value="live" size="small" />
            <n-tag v-if="live || liveState !== 'closed'" size="small" :bordered="false" :type="liveTagType">
              {{ liveStateText }}
            </n-tag>
          </div>
          <span class="dim logs-hint">一次性加载最近 500 行;开启实时后断线自动重连(上限 30s)</span>
        </div>
        <n-alert v-if="logsError" type="error" style="margin-bottom: 10px">{{ logsError }}</n-alert>
        <n-spin :show="logsLoading">
          <LogViewer :lines="logLines" height="420px" placeholder="暂无日志输出" />
        </n-spin>
      </n-tab-pane>

      <n-tab-pane name="stats" tab="统计">
        <n-alert v-if="statsError" type="error" style="margin-bottom: 10px">{{ statsError }}</n-alert>
        <div class="stats-grid">
          <n-card size="small" title="CPU 使用率" class="stats-card">
            <template #header-extra>
              <span class="dim">{{ lastStat ? `${lastStat.cpu_percent.toFixed(2)}%` : '—' }}</span>
            </template>
            <Sparkline
              v-if="cpuSeries.length"
              :points="cpuSeries"
              :width="560"
              :height="140"
              color="#8f9bff"
              class="chart-lg"
            />
            <n-empty v-else description="暂无采样数据(容器可能未运行)" />
          </n-card>
          <n-card size="small" title="内存" class="stats-card">
            <template #header-extra>
              <span class="dim">
                {{ lastStat ? `${fmtMB(lastStat.mem_mb)} / ${fmtMB(lastStat.mem_limit_mb)}` : '—' }}
              </span>
            </template>
            <Sparkline
              v-if="memSeries.length"
              :points="memSeries"
              :width="560"
              :height="140"
              color="#5fd4b0"
              class="chart-lg"
            />
            <n-empty v-else description="暂无采样数据(容器可能未运行)" />
          </n-card>
        </div>
        <div class="dim stats-hint">采样点:{{ stats.length }}(后台每 10s 采样,保留最近 1 小时)</div>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.detail-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.id-text {
  word-break: break-all;
}

.port-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}

.extra-hint {
  font-size: 12px;
}

.logs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.logs-toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logs-hint {
  font-size: 12px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 12px;
}

.stats-card {
  min-width: 0;
}

.chart-lg {
  width: 100%;
  height: auto;
}

.stats-hint {
  font-size: 12px;
  margin-top: 10px;
}
</style>
