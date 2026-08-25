<script setup lang="ts">
import { h, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSpace,
  NSwitch,
  NTag,
  type DataTableColumns,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import { message } from '../utils/feedback';
import { fmtDateTime, fmtRelative } from '../utils/format';

interface KeyItem {
  id: number;
  name: string;
  key_prefix: string;
  scopes: string[];
  enabled: boolean;
  created_at: string;
  last_used_at: string | null;
}

interface KeysResp {
  total: number;
  items: KeyItem[];
}

interface CreateResp {
  id: number;
  key: string;
}

const SCOPE_OPTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: 'view', label: 'view', hint: '只读:列表/详情/日志/任务' },
  { value: 'start', label: 'start', hint: '启动类:启动/重启/恢复/栈 up' },
  { value: 'stop', label: 'stop', hint: '停止类:停止/暂停/栈 down' },
  { value: 'delete', label: 'delete', hint: '删除类:删除容器、栈删卷' },
  { value: 'admin', label: 'admin', hint: '管理:隐含以上全部,含 Key/审计' },
];

const items = ref<KeyItem[]>([]);
const errorMsg = ref('');
const loading = ref(false);
const loaded = ref(false);

/** 写接口仅 JWT 用户可用,API Key 主体 403 */
function opError(e: unknown): void {
  const err = e as ApiError;
  if (err.status === 403) {
    message.warning('请用管理员账号登录操作');
  } else {
    message.error(err.message);
  }
}

async function load(): Promise<void> {
  loading.value = true;
  try {
    const r = await api<KeysResp>('/keys');
    items.value = r.items ?? [];
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
    loaded.value = true;
  }
}

onMounted(() => void load());

/* ---------------- 新建 ---------------- */

const createShow = ref(false);
const createName = ref('');
const createScopes = ref<string[]>(['view']);
const createBusy = ref(false);

function openCreate(): void {
  createName.value = '';
  createScopes.value = ['view'];
  createShow.value = true;
}

async function submitCreate(): Promise<boolean> {
  const name = createName.value.trim();
  if (!name) {
    message.warning('请输入 Key 名称');
    return false;
  }
  if (createScopes.value.length === 0) {
    message.warning('请至少勾选一个 scope');
    return false;
  }
  createBusy.value = true;
  try {
    const r = await api<CreateResp>('/keys', {
      method: 'POST',
      body: { name, scopes: createScopes.value },
    });
    createShow.value = false;
    createdKey.value = r.key;
    keyShow.value = true;
    void load();
    return true;
  } catch (e) {
    opError(e);
    return false;
  } finally {
    createBusy.value = false;
  }
}

/* ---------------- 一次性明文展示 ---------------- */

const keyShow = ref(false);
const createdKey = ref('');

async function copyCreated(): Promise<void> {
  try {
    await navigator.clipboard.writeText(createdKey.value);
    message.success('已复制到剪贴板');
  } catch {
    message.error('复制失败,请手动选中复制');
  }
}

/* ---------------- 启停 / 编辑 / 删除 ---------------- */

const switchBusy = ref<Record<number, boolean>>({});

async function toggleEnabled(item: KeyItem, value: boolean): Promise<void> {
  if (switchBusy.value[item.id]) return;
  switchBusy.value = { ...switchBusy.value, [item.id]: true };
  try {
    await api(`/keys/${item.id}`, { method: 'PATCH', body: { enabled: value } });
    item.enabled = value;
    message.success(value ? 'Key 已启用' : 'Key 已禁用');
  } catch (e) {
    opError(e);
  } finally {
    const next = { ...switchBusy.value };
    delete next[item.id];
    switchBusy.value = next;
  }
}

const editShow = ref(false);
const editBusy = ref(false);
const editId = ref<number | null>(null);
const editName = ref('');
const editScopes = ref<string[]>([]);

function openEdit(item: KeyItem): void {
  editId.value = item.id;
  editName.value = item.name;
  editScopes.value = [...item.scopes];
  editShow.value = true;
}

async function submitEdit(): Promise<boolean> {
  if (editId.value === null) return true;
  const name = editName.value.trim();
  if (!name) {
    message.warning('请输入 Key 名称');
    return false;
  }
  if (editScopes.value.length === 0) {
    message.warning('请至少勾选一个 scope');
    return false;
  }
  editBusy.value = true;
  try {
    await api(`/keys/${editId.value}`, {
      method: 'PATCH',
      body: { name, scopes: editScopes.value },
    });
    message.success('已保存');
    editShow.value = false;
    void load();
    return true;
  } catch (e) {
    opError(e);
    return false;
  } finally {
    editBusy.value = false;
  }
}

const delShow = ref(false);
const delBusy = ref(false);
const delItem = ref<KeyItem | null>(null);

function openDelete(item: KeyItem): void {
  delItem.value = item;
  delShow.value = true;
}

async function confirmDelete(): Promise<boolean> {
  const item = delItem.value;
  if (!item) return true;
  delBusy.value = true;
  try {
    await api(`/keys/${item.id}`, { method: 'DELETE' });
    message.success('Key 已删除');
    delShow.value = false;
    void load();
    return true;
  } catch (e) {
    opError(e);
    return false;
  } finally {
    delBusy.value = false;
  }
}

/* ---------------- 表格 ---------------- */

function renderScopes(scopes: string[]): ReturnType<typeof h> {
  const tags = scopes.map((s) =>
    h(
      NTag,
      {
        key: s,
        size: 'small',
        bordered: false,
        type: s === 'admin' ? 'warning' : 'info',
      },
      { default: () => s }
    )
  );
  return h(NSpace, { size: 4, wrap: true, style: 'gap: 4px 4px' }, { default: () => tags });
}

const columns: DataTableColumns<KeyItem> = [
  {
    title: '名称',
    key: 'name',
    minWidth: 160,
    render(row) {
      return h('span', { style: 'font-weight: 500' }, row.name);
    },
  },
  {
    title: 'Key 前缀',
    key: 'key_prefix',
    width: 130,
    render(row) {
      return h('span', { class: 'mono dim' }, row.key_prefix);
    },
  },
  {
    title: 'Scopes',
    key: 'scopes',
    minWidth: 200,
    render(row) {
      return renderScopes(row.scopes ?? []);
    },
  },
  {
    title: '启用',
    key: 'enabled',
    width: 90,
    render(row) {
      return h(NSwitch, {
        size: 'small',
        value: row.enabled,
        loading: !!switchBusy.value[row.id],
        'onUpdate:value': (v: boolean) => void toggleEnabled(row, v),
      });
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
    title: '最近使用',
    key: 'last_used_at',
    width: 130,
    render(row) {
      return row.last_used_at
        ? h('span', { title: fmtDateTime(row.last_used_at) }, fmtRelative(row.last_used_at))
        : h('span', { class: 'dim' }, '从未使用');
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 130,
    fixed: 'right',
    render(row) {
      return h('div', { class: 'row-actions' }, [
        h(
          NButton,
          { size: 'tiny', quaternary: true, onClick: () => openEdit(row) },
          { default: () => '编辑' }
        ),
        h(
          NButton,
          { size: 'tiny', quaternary: true, style: 'color: #e88080', onClick: () => openDelete(row) },
          { default: () => '删除' }
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
        <h2 class="page-title">API Key</h2>
        <div class="page-subtitle">
          {{ items.length }} 个 Key · 写操作(创建/修改/删除)仅限管理员账号登录使用
        </div>
      </div>
      <div class="page-actions">
        <n-button size="small" :loading="loading" @click="load">刷新</n-button>
        <n-button size="small" type="primary" @click="openCreate">新建 Key</n-button>
      </div>
    </div>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 12px">{{ errorMsg }}</n-alert>

    <n-card content-style="padding: 4px 8px" :bordered="true">
      <n-data-table
        size="small"
        :columns="columns"
        :data="items"
        :row-key="(r: KeyItem) => r.id"
        :loading="loading && !loaded"
        :scroll-x="980"
      />
    </n-card>

    <!-- 新建 -->
    <n-modal
      v-model:show="createShow"
      preset="dialog"
      title="新建 API Key"
      positive-text="创建"
      negative-text="取消"
      :loading="createBusy"
      @positive-click="submitCreate"
      @negative-click="createShow = false"
    >
      <n-form label-placement="top" style="margin-top: 8px">
        <n-form-item label="名称" required>
          <n-input
            v-model:value="createName"
            placeholder="用途辨识名,如 mobile-app"
            maxlength="64"
            @keyup.enter="submitCreate"
          />
        </n-form-item>
        <n-form-item label="Scopes(至少一个)" required>
          <n-checkbox-group v-model:value="createScopes">
            <div class="scope-list">
              <div v-for="s in SCOPE_OPTIONS" :key="s.value" class="scope-item">
                <n-checkbox :value="s.value" :label="s.label" />
                <span class="dim scope-hint">{{ s.hint }}</span>
              </div>
            </div>
          </n-checkbox-group>
        </n-form-item>
      </n-form>
    </n-modal>

    <!-- 一次性明文 -->
    <n-modal
      v-model:show="keyShow"
      preset="card"
      title="Key 创建成功"
      class="key-modal"
      :mask-closable="false"
      :closable="true"
    >
      <n-alert type="warning" title="明文仅此一次显示" style="margin-bottom: 12px">
        关闭后任何接口都无法再次查看该 Key,请立即复制并妥善保存。数据库仅存 SHA-256 哈希。
      </n-alert>
      <div class="key-box mono">{{ createdKey }}</div>
      <template #footer>
        <div class="key-footer">
          <n-button type="primary" @click="copyCreated">一键复制</n-button>
          <n-button quaternary @click="keyShow = false">我已妥善保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 编辑 -->
    <n-modal
      v-model:show="editShow"
      preset="dialog"
      title="编辑 API Key"
      positive-text="保存"
      negative-text="取消"
      :loading="editBusy"
      @positive-click="submitEdit"
      @negative-click="editShow = false"
    >
      <n-form label-placement="top" style="margin-top: 8px">
        <n-form-item label="名称" required>
          <n-input v-model:value="editName" maxlength="64" placeholder="Key 名称" />
        </n-form-item>
        <n-form-item label="Scopes(至少一个)" required>
          <n-checkbox-group v-model:value="editScopes">
            <div class="scope-list">
              <div v-for="s in SCOPE_OPTIONS" :key="s.value" class="scope-item">
                <n-checkbox :value="s.value" :label="s.label" />
                <span class="dim scope-hint">{{ s.hint }}</span>
              </div>
            </div>
          </n-checkbox-group>
        </n-form-item>
      </n-form>
    </n-modal>

    <!-- 删除确认 -->
    <n-modal
      v-model:show="delShow"
      preset="dialog"
      type="warning"
      title="删除 API Key"
      :loading="delBusy"
      positive-text="删除"
      negative-text="取消"
      @positive-click="confirmDelete"
      @negative-click="delShow = false"
    >
      <p style="margin: 0">
        确定删除 Key
        <code class="mono">{{ delItem?.name }}</code
        >(<span class="mono dim">{{ delItem?.key_prefix }}…</span>)吗?使用该 Key 的调用将立即失效,该操作不可撤销。
      </p>
    </n-modal>
  </div>
</template>

<style scoped>
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.scope-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.scope-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.scope-hint {
  font-size: 12px;
}

.key-modal {
  width: 560px;
  max-width: calc(100vw - 48px);
}

.key-box {
  background: #0a0b0e;
  border: 1px solid #262833;
  border-radius: 6px;
  padding: 14px 16px;
  font-size: 14px;
  word-break: break-all;
  color: #8f9bff;
}

.key-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: flex-end;
}
</style>
