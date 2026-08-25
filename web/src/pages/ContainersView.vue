<script setup lang="ts">
import { computed, h, ref } from 'vue';
import { RouterLink, useRouter } from 'vue-router';
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NDropdown,
  NInput,
  NModal,
  NTag,
  type DataTableColumns,
  type DropdownOption,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import Sparkline from '../components/Sparkline.vue';
import StateBadge from '../components/StateBadge.vue';
import { usePoll } from '../composables/usePoll';
import { message } from '../utils/feedback';
import { fmtDateTime, fmtMB, fmtRelative } from '../utils/format';
import type { ContainerItem } from '../types';

const router = useRouter();

type ContainerAction = 'start' | 'stop' | 'restart' | 'pause' | 'unpause';

const items = ref<ContainerItem[]>([]);
const search = ref('');
const errorMsg = ref('');
const refreshing = ref(false);
const firstLoading = ref(true);
const busy = ref<Record<string, boolean>>({});

async function load(): Promise<void> {
  try {
    const list = await api<ContainerItem[]>('/containers');
    items.value = Array.isArray(list) ? list : [];
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

const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return items.value;
  return items.value.filter(
    (it) =>
      (it.name ?? '').toLowerCase().includes(q) ||
      (it.image ?? '').toLowerCase().includes(q) ||
      (it.compose_project ?? '').toLowerCase().includes(q)
  );
});

const runningCount = computed(() => items.value.filter((i) => i.state === 'running').length);

/* ---------------- 操作 ---------------- */

async function doAction(item: ContainerItem, action: ContainerAction): Promise<void> {
  const key = `${item.id}:${action}`;
  if (busy.value[key]) return;
  busy.value = { ...busy.value, [key]: true };
  try {
    const r = await api<{ status?: string; note?: string }>(
      `/containers/${encodeURIComponent(item.id)}/${action}`,
      { method: 'POST' }
    );
    if (r && r.note === 'already_in_state') {
      message.info('已在目标状态');
    } else {
      message.success('操作成功');
    }
    await refresh(); // 操作后立即手动刷新,不等轮询
  } catch (e) {
    // 409/404 等直接展示 daemon 返回的 message 原文
    message.error((e as ApiError).message);
  } finally {
    const next = { ...busy.value };
    delete next[key];
    busy.value = next;
  }
}

/* ---------------- 删除 ---------------- */

const delShow = ref(false);
const delForce = ref(false);
const delBusy = ref(false);
const delItem = ref<ContainerItem | null>(null);

function openDelete(item: ContainerItem): void {
  delItem.value = item;
  delForce.value = false;
  delShow.value = true;
}

async function confirmDelete(): Promise<boolean> {
  const item = delItem.value;
  if (!item) return true;
  delBusy.value = true;
  try {
    await api(`/containers/${encodeURIComponent(item.id)}`, {
      method: 'DELETE',
      query: { force: delForce.value },
    });
    message.success('容器已删除');
    delShow.value = false;
    await refresh();
    return true;
  } catch (e) {
    message.error((e as ApiError).message);
    return false;
  } finally {
    delBusy.value = false;
  }
}

function onMore(item: ContainerItem, key: string | number): void {
  if (key === 'delete') {
    openDelete(item);
  } else {
    void doAction(item, key as ContainerAction);
  }
}

const moreOptions: DropdownOption[] = [
  { label: '暂停', key: 'pause' },
  { label: '恢复', key: 'unpause' },
  { type: 'divider', key: 'divider' },
  { label: () => h('span', { style: 'color: #e88080' }, '删除'), key: 'delete' },
];

/* ---------------- 表格 ---------------- */

const columns: DataTableColumns<ContainerItem> = [
  {
    title: '状态',
    key: 'state',
    width: 100,
    render(row) {
      return h(StateBadge, { state: row.state });
    },
  },
  {
    title: '名称',
    key: 'name',
    minWidth: 220,
    render(row) {
      return h('div', { class: 'name-cell' }, [
        h(
          NButton,
          {
            size: 'tiny',
            type: 'primary',
            secondary: true,
            onClick: () => void router.push(`/containers/${encodeURIComponent(row.id)}`),
          },
          { default: () => '详情' }
        ),
        h(
          RouterLink,
          { to: `/containers/${encodeURIComponent(row.id)}`, class: 'name-link' },
          { default: () => row.name || row.id.slice(0, 12) }
        ),
        row.is_self
          ? h(NTag, { size: 'small', bordered: false, type: 'warning' }, { default: () => '面板自身' })
          : null,
        h('div', { class: 'dim mono id-sub' }, row.id.slice(0, 12)),
      ]);
    },
  },
  {
    title: '镜像',
    key: 'image',
    minWidth: 180,
    ellipsis: { tooltip: true },
    render(row) {
      return h('span', { class: 'mono' }, row.image || '—');
    },
  },
  {
    title: 'Compose 项目',
    key: 'compose_project',
    width: 150,
    render(row) {
      return row.compose_project
        ? h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => row.compose_project })
        : h('span', { class: 'dim' }, '—');
    },
  },
  {
    title: '创建时间',
    key: 'created',
    width: 130,
    render(row) {
      return h('span', { title: fmtDateTime(row.created) }, fmtRelative(row.created));
    },
  },
  {
    title: 'CPU',
    key: 'cpu',
    width: 170,
    render(row) {
      const pts = (row.stats ?? []).slice(-30).map((s) => s.cpu_percent ?? 0);
      const last = pts.length ? pts[pts.length - 1] : null;
      return h('div', { class: 'spark-cell' }, [
        h(Sparkline, { points: pts, width: 96, height: 26, color: '#8f9bff' }),
        h('span', { class: 'spark-val' }, last == null ? '—' : `${last.toFixed(1)}%`),
      ]);
    },
  },
  {
    title: '内存',
    key: 'mem',
    width: 180,
    render(row) {
      const stats = row.stats ?? [];
      const pts = stats.slice(-30).map((s) => s.mem_mb ?? 0);
      const last = stats.length ? stats[stats.length - 1] : null;
      const limit = last?.mem_limit_mb ?? null;
      return h('div', { class: 'spark-cell' }, [
        h(Sparkline, { points: pts, width: 96, height: 26, color: '#5fd4b0' }),
        h(
          'span',
          { class: 'spark-val' },
          last == null ? '—' : limit ? `${fmtMB(last.mem_mb)} / ${fmtMB(limit)}` : fmtMB(last.mem_mb)
        ),
      ]);
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    fixed: 'right',
    render(row) {
      // 面板自身容器: 禁止一切变更操作(停掉自己=整个服务中断,后端同样强制拦截)
      const self = row.is_self === true;
      const actionBtn = (action: ContainerAction, label: string, enabled: boolean) =>
        h(
          NButton,
          {
            size: 'tiny',
            quaternary: true,
            disabled: self || !enabled || !!busy.value[`${row.id}:${action}`],
            loading: !!busy.value[`${row.id}:${action}`],
            onClick: () => void doAction(row, action),
          },
          { default: () => label }
        );
      return h('div', { class: 'row-actions', title: self ? '面板自身容器,禁止变更操作' : undefined }, [
        actionBtn('start', '启动', row.state !== 'running'),
        actionBtn('stop', '停止', row.state === 'running' || row.state === 'paused'),
        actionBtn('restart', '重启', true),
        h(
          NDropdown,
          {
            options: moreOptions,
            trigger: 'click',
            disabled: self,
            onSelect: (key: string | number) => onMore(row, key),
          },
          {
            default: () =>
              h(NButton, { size: 'tiny', quaternary: true, disabled: self }, { default: () => '更多' }),
          }
        ),
      ]);
    },
  },
];
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">容器</h2>
        <div class="page-subtitle">{{ items.length }} 个容器 · {{ runningCount }} 个运行中 · 每 5 秒自动刷新</div>
      </div>
      <div class="page-actions">
        <n-input
          v-model:value="search"
          size="small"
          clearable
          placeholder="名称 / 镜像 / 项目过滤"
          style="width: 220px"
        />
        <n-button size="small" :loading="refreshing" @click="refreshNow">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">{{ errorMsg }}</n-alert>

    <n-card content-style="padding: 4px 8px" :bordered="true">
      <n-data-table
        size="small"
        :columns="columns"
        :data="filteredItems"
        :row-key="(r: ContainerItem) => r.id"
        :loading="firstLoading"
        :scroll-x="1320"
      />
    </n-card>

    <n-modal
      v-model:show="delShow"
      preset="dialog"
      type="warning"
      title="删除容器"
      :loading="delBusy"
      positive-text="删除"
      negative-text="取消"
      @positive-click="confirmDelete"
      @negative-click="delShow = false"
    >
      <p style="margin: 0 0 10px">
        确定删除容器
        <code class="mono">{{ delItem?.name }}</code>
        吗?该操作不可撤销。
      </p>
      <n-checkbox v-model:checked="delForce">强制删除(运行中的容器将被终止后删除)</n-checkbox>
    </n-modal>
  </div>
</template>

<style scoped>
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.name-cell {
  min-width: 0;
}

.name-link {
  font-weight: 500;
  color: #8f9bff;
}

.name-link:hover {
  text-decoration: underline;
}

.id-sub {
  font-size: 11px;
}

.spark-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.spark-val {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.65);
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}
</style>
