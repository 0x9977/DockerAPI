<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { NAlert, NSpin, NTag } from 'naive-ui';
import { api, ApiError } from '../api/client';
import type { VersionInfo } from '../types';
import { fmtCount } from '../utils/format';

const info = ref<VersionInfo | null>(null);
const error = ref('');
const loading = ref(true);
const APP_VERSION = __APP_VERSION__;

async function load(): Promise<void> {
  try {
    info.value = await api<VersionInfo>('/version');
    error.value = '';
  } catch (e) {
    const err = e as ApiError;
    // 401/503 已由 client 统一跳转,不作为页面错误展示
    if (err.status !== 401 && err.status !== 503) error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(() => void load());

const versionMismatch = computed(() => {
  const p = info.value?.panel;
  return !!p && p !== APP_VERSION;
});

const metaCards = computed(() => {
  const v = info.value;
  return [
    { label: '面板版本', value: v?.panel ?? '—', sub: '', mono: false },
    { label: 'Docker 版本', value: v?.docker ?? '—', sub: v?.api_version ? `API ${v.api_version}` : '', mono: false },
    { label: '操作系统', value: v?.os ?? '—', sub: '', mono: false },
    { label: '连接方式', value: v?.docker_host ?? '—', sub: '', mono: true },
    { label: '存储驱动', value: v?.storage_driver ?? '—', sub: '', mono: false },
    { label: '镜像数', value: fmtCount(v?.images_count), sub: '', mono: false },
    { label: '卷数', value: fmtCount(v?.volumes_count), sub: '', mono: false },
  ];
});

const containerStats = computed(() => {
  const s = info.value?.containers_summary;
  return [
    { label: '运行中', value: fmtCount(s?.running), color: '#63e2b7' },
    { label: '已暂停', value: fmtCount(s?.paused), color: '#f2c97d' },
    { label: '已停止', value: fmtCount(s?.stopped), color: '#a2a5b0' },
    { label: '全部容器', value: fmtCount(s?.all), color: '#70c0e8' },
  ];
});
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">总览</h2>
        <div class="page-subtitle">面板与 Docker daemon 概览</div>
      </div>
      <n-tag v-if="versionMismatch" type="warning" size="small" :bordered="false">
        前端 v{{ APP_VERSION }} 与面板 {{ info?.panel }} 不一致
      </n-tag>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 14px">
      {{ error }}
    </n-alert>
    <n-alert v-else-if="info?.error" type="warning" title="Docker daemon 不可达" style="margin-bottom: 14px">
      {{ info.error }}
    </n-alert>

    <n-spin :show="loading">
      <div class="stat-grid">
        <div v-for="c in metaCards" :key="c.label" class="stat-card">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value" :class="{ mono: c.mono }" :title="c.value">{{ c.value }}</div>
          <div v-if="c.sub" class="stat-sub dim">{{ c.sub }}</div>
        </div>
      </div>

      <div class="section-title">容器</div>
      <div class="stat-grid">
        <div v-for="c in containerStats" :key="c.label" class="stat-card">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value stat-value--big" :style="{ color: c.color }">{{ c.value }}</div>
        </div>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 8px;
}

.stat-card {
  background: #15161c;
  border: 1px solid #262833;
  border-radius: 8px;
  padding: 14px 16px;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
  margin-bottom: 8px;
}

.stat-value {
  font-size: 16px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stat-value--big {
  font-size: 28px;
}

.stat-sub {
  font-size: 12px;
  margin-top: 6px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  margin: 18px 0 10px;
}
</style>
