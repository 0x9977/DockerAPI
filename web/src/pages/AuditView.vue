<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NInput,
  NSelect,
  NTag,
  NTooltip,
  type DataTableColumns,
  type PaginationProps,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import { fmtDateTime } from '../utils/format';

interface AuditItem {
  id: number;
  ts: string;
  actor_type: string; // user | api_key | system
  actor_name: string;
  action: string;
  target_type: string;
  target_id: string;
  result: string; // success | error
  detail: string | null;
  ip: string | null;
}

interface AuditResp {
  total: number;
  items: AuditItem[];
}

const ACTION_GROUPS: Array<{ label: string; actions: Array<[string, string]> }> = [
  {
    label: '容器',
    actions: [
      ['container.start', '启动容器'],
      ['container.stop', '停止容器'],
      ['container.restart', '重启容器'],
      ['container.pause', '暂停容器'],
      ['container.unpause', '恢复容器'],
      ['container.remove', '删除容器'],
    ],
  },
  {
    label: '栈',
    actions: [
      ['stack.up', '启动栈'],
      ['stack.down', '停止栈'],
      ['stack.restart', '重启栈'],
    ],
  },
  {
    label: 'API Key',
    actions: [
      ['key.create', '创建 Key'],
      ['key.update', '修改 Key'],
      ['key.delete', '删除 Key'],
    ],
  },
  {
    label: '认证',
    actions: [
      ['auth.login', '登录(仅失败)'],
      ['auth.setup', '初始化'],
    ],
  },
  {
    label: '任务',
    actions: [['job.timeout', '任务超时']],
  },
];

const actionOptions = ACTION_GROUPS.flatMap((g) =>
  g.actions.map(([value, label]) => ({ value, label: `${value}(${label})` }))
);

const PAGE_SIZE = 20;

const items = ref<AuditItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(PAGE_SIZE);
const loading = ref(false);
const errorMsg = ref('');

/* 过滤器 */
const filterActor = ref('');
const filterAction = ref<string | null>(null);
const filterTarget = ref('');

let debounceTimer: ReturnType<typeof setTimeout> | null = null;

watch([filterActor, filterAction, filterTarget], () => {
  if (debounceTimer !== null) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 400);
});

onBeforeUnmount(() => {
  if (debounceTimer !== null) clearTimeout(debounceTimer);
});

async function load(): Promise<void> {
  loading.value = true;
  try {
    const r = await api<AuditResp>('/audit', {
      query: {
        page: page.value,
        page_size: pageSize.value,
        actor: filterActor.value.trim() || undefined,
        action: filterAction.value || undefined,
        target: filterTarget.value.trim() || undefined,
      },
    });
    items.value = r.items ?? [];
    total.value = r.total ?? 0;
    errorMsg.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status === 403) {
      errorMsg.value = '需要 admin 权限,请用管理员账号登录后查看';
    } else if (err.status !== 401 && err.status !== 503) {
      errorMsg.value = err.message;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => void load());

function resetFilters(): void {
  filterActor.value = '';
  filterAction.value = null;
  filterTarget.value = '';
  // watch 触发防抖加载
}

const pagination = computed<PaginationProps>(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  pageSizes: [20, 50, 100],
  showSizePicker: true,
  onChange: (p: number) => {
    page.value = p;
    void load();
  },
  onUpdatePageSize: (s: number) => {
    pageSize.value = s;
    page.value = 1;
    void load();
  },
}));

function actorTagType(type: string): 'info' | 'warning' | 'default' {
  if (type === 'user') return 'info';
  if (type === 'api_key') return 'warning';
  return 'default';
}

const columns: DataTableColumns<AuditItem> = [
  {
    title: '时间',
    key: 'ts',
    width: 165,
    render(row) {
      return h('span', { class: 'mono time-cell' }, fmtDateTime(row.ts));
    },
  },
  {
    title: '主体',
    key: 'actor',
    minWidth: 170,
    render(row) {
      return h('div', { class: 'actor-cell' }, [
        h(
          NTag,
          { size: 'small', bordered: false, type: actorTagType(row.actor_type) },
          { default: () => row.actor_type }
        ),
        h('span', { class: 'mono actor-name', title: row.actor_name }, row.actor_name || '—'),
      ]);
    },
  },
  {
    title: '动作',
    key: 'action',
    width: 165,
    render(row) {
      return h('span', { class: 'mono' }, row.action);
    },
  },
  {
    title: '目标',
    key: 'target',
    minWidth: 180,
    render(row) {
      return h('div', { class: 'target-cell' }, [
        h('span', { class: 'dim target-type' }, row.target_type),
        h('span', { class: 'mono target-id', title: row.target_id }, row.target_id || '—'),
      ]);
    },
  },
  {
    title: '结果',
    key: 'result',
    width: 90,
    render(row) {
      const ok = row.result === 'success';
      return h(
        NTag,
        { size: 'small', bordered: false, round: true, type: ok ? 'success' : 'error' },
        { default: () => (ok ? '成功' : '失败') }
      );
    },
  },
  {
    title: '详情',
    key: 'detail',
    minWidth: 200,
    render(row) {
      if (!row.detail) return h('span', { class: 'dim' }, '—');
      return h(
        NTooltip,
        { style: 'max-width: 420px', placement: 'top' },
        {
          trigger: () =>
            h('span', { class: 'mono detail-cell' }, row.detail ?? ''),
          default: () => row.detail,
        }
      );
    },
  },
  {
    title: 'IP',
    key: 'ip',
    width: 130,
    render(row) {
      return h('span', { class: 'mono dim' }, row.ip || '—');
    },
  },
];

const pageStart = computed(() => (total.value === 0 ? 0 : (page.value - 1) * pageSize.value + 1));
const pageEnd = computed(() => (page.value - 1) * pageSize.value + items.value.length);
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">审计日志</h2>
        <div class="page-subtitle">
          共 {{ total }} 条 · 当前第 {{ pageStart }}-{{ pageEnd }} 条 · 仅记录变更类操作
        </div>
      </div>
      <div class="page-actions">
        <n-button size="small" @click="resetFilters">重置过滤</n-button>
        <n-button size="small" :loading="loading" @click="load">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">{{ errorMsg }}</n-alert>

    <n-card content-style="padding: 14px 14px 4px" :bordered="true" style="margin-bottom: 12px">
      <div class="filters">
        <n-input
          v-model:value="filterActor"
          size="small"
          clearable
          placeholder="主体(用户名 / Key 名称)"
          class="filter-item"
          @keyup.enter="page = 1; load()"
        />
        <n-select
          v-model:value="filterAction"
          size="small"
          clearable
          filterable
          placeholder="动作(全部)"
          :options="actionOptions"
          class="filter-item filter-action"
        />
        <n-input
          v-model:value="filterTarget"
          size="small"
          clearable
          placeholder="目标(容器 / 栈名 / Key id)"
          class="filter-item"
          @keyup.enter="page = 1; load()"
        />
      </div>
      <div class="dim filter-hint">输入后自动查询(400ms 防抖);动作下拉支持键入过滤</div>
    </n-card>

    <n-card content-style="padding: 4px 8px" :bordered="true">
      <n-data-table
        size="small"
        remote
        :columns="columns"
        :data="items"
        :row-key="(r: AuditItem) => r.id"
        :loading="loading"
        :pagination="pagination"
        :scroll-x="1120"
      />
    </n-card>
  </div>
</template>

<style scoped>
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-item {
  width: 240px;
}

.filter-action {
  width: 280px;
}

.filter-hint {
  font-size: 12px;
  margin: 8px 2px 10px;
}

.time-cell {
  font-size: 12px;
}

.actor-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.actor-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.target-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.target-type {
  flex-shrink: 0;
  font-size: 11px;
}

.target-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.detail-cell {
  display: inline-block;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  vertical-align: bottom;
}
</style>
