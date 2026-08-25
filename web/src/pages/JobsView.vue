<script setup lang="ts">
import { h, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NTag,
  type DataTableColumns,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import JobDrawer from '../components/JobDrawer.vue';
import JobOutput from '../components/JobOutput.vue';
import {
  jobStatusLabel,
  jobStatusTagType,
  jobTypeLabel,
  type JobItem,
} from '../components/job';
import { usePoll } from '../composables/usePoll';
import { message } from '../utils/feedback';
import { fmtDateTime, fmtRelative } from '../utils/format';

interface JobsResp {
  total: number;
  items: JobItem[];
}

/** 展示最近 50 条(任务保留 7 天,足够覆盖常规排查) */
const PAGE_SIZE = 50;

const items = ref<JobItem[]>([]);
const total = ref(0);
const errorMsg = ref('');
const refreshing = ref(false);
const firstLoading = ref(true);

const expandedKeys = ref<Array<string | number>>([]);
/** 从 /jobs?job=<id> 深链进入时,该行展开且默认打开实时流 */
const autoLiveId = ref('');

/* ---------------- ?job=<id> 深链处理 ---------------- */

const route = useRoute();
const drawerShow = ref(false);
const drawerJobId = ref<string | null>(null);
let queryHandledFor = '';

function currentQueryJob(): string {
  const q = route.query.job;
  return typeof q === 'string' && q ? q : '';
}

async function handleQueryJob(): Promise<void> {
  const jobId = currentQueryJob();
  if (!jobId || queryHandledFor === jobId) return;
  queryHandledFor = jobId;
  const found = items.value.find((j) => j.id === jobId);
  if (found) {
    expandedKeys.value = [jobId];
    autoLiveId.value = jobId;
    return;
  }
  // 不在当前列表(较旧)→ 拉详情用抽屉展示
  try {
    await api<JobItem>(`/jobs/${encodeURIComponent(jobId)}`);
    drawerJobId.value = jobId;
    drawerShow.value = true;
  } catch {
    message.warning(`未找到指定任务: ${jobId}`);
  }
}

watch(
  () => route.query.job,
  () => void handleQueryJob()
);

/* ---------------- 列表(5s 轮询) ---------------- */

async function load(): Promise<void> {
  try {
    const r = await api<JobsResp>('/jobs', { query: { page: 1, page_size: PAGE_SIZE } });
    items.value = r.items ?? [];
    total.value = r.total ?? items.value.length;
    errorMsg.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) errorMsg.value = err.message;
  } finally {
    firstLoading.value = false;
  }
  await handleQueryJob();
}

const { refresh } = usePoll(load, 5000);

async function refreshNow(): Promise<void> {
  refreshing.value = true;
  await refresh();
  refreshing.value = false;
}

const runningCount = ref(0);

function recountRunning(): void {
  runningCount.value = items.value.filter((j) => j.status === 'running' || j.status === 'queued')
    .length;
}

watch(items, recountRunning, { immediate: true });

/* ---------------- 表格 ---------------- */

function fmtDuration(from?: string | null, to?: string | null): string {
  if (!from) return '—';
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const s = Math.floor((end - start) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60}s`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

const columns: DataTableColumns<JobItem> = [
  {
    type: 'expand',
    renderExpand(row) {
      return h(
        'div',
        { style: 'padding: 4px 12px 12px' },
        [h(JobOutput, { job: row, autoLive: row.id === autoLiveId.value })]
      );
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 96,
    render(row) {
      return h(
        NTag,
        { size: 'small', bordered: false, round: true, type: jobStatusTagType(row.status) },
        { default: () => jobStatusLabel(row.status) }
      );
    },
  },
  {
    title: '任务类型',
    key: 'type',
    width: 170,
    render(row) {
      return h('span', { class: 'mono type-cell' }, jobTypeLabel(row.type));
    },
  },
  {
    title: '栈',
    key: 'stack',
    minWidth: 140,
    render(row) {
      return h('span', { class: 'mono' }, row.stack || '—');
    },
  },
  {
    title: '退出码',
    key: 'exit_code',
    width: 90,
    render(row) {
      if (row.exit_code === null || row.exit_code === undefined) {
        return h('span', { class: 'dim' }, '—');
      }
      return h(
        'span',
        { class: 'mono', style: row.exit_code === 0 ? '' : 'color: #e88080' },
        String(row.exit_code)
      );
    },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 130,
    render(row) {
      return h('span', { title: fmtDateTime(row.created_at) }, fmtRelative(row.created_at));
    },
  },
  {
    title: '耗时',
    key: 'duration',
    width: 100,
    render(row) {
      return h(
        'span',
        { class: 'mono' },
        fmtDuration(row.started_at ?? row.created_at, row.finished_at)
      );
    },
  },
];

function onExpandedKeysUpdate(keys: Array<string | number>): void {
  expandedKeys.value = keys;
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">任务中心</h2>
        <div class="page-subtitle">
          {{ items.length }} / {{ total }} 条任务 · {{ runningCount }} 个进行中 · 每 5 秒自动刷新
        </div>
      </div>
      <div class="page-actions">
        <n-button size="small" :loading="refreshing" @click="refreshNow">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">{{ errorMsg }}</n-alert>

    <n-card content-style="padding: 4px 8px" :bordered="true">
      <n-data-table
        size="small"
        :columns="columns"
        :data="items"
        :row-key="(r: JobItem) => r.id"
        :loading="firstLoading"
        :expanded-row-keys="expandedKeys"
        :scroll-x="820"
        @update:expanded-row-keys="onExpandedKeysUpdate"
      />
    </n-card>

    <div class="dim jobs-hint">
      点击行首箭头展开输出;进行中任务自动订阅实时流(结束自动断开),已终结任务可手动开关重放
    </div>

    <!-- 深链到不在当前列表的任务时用抽屉展示 -->
    <JobDrawer v-model:show="drawerShow" :job-id="drawerJobId" />
  </div>
</template>

<style scoped>
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-cell {
  font-size: 12px;
}

.jobs-hint {
  font-size: 12px;
  margin-top: 8px;
}
</style>
