<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import {
  NAlert,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NSpin,
  NTag,
} from 'naive-ui';
import { api } from '../api/client';
import JobOutput from './JobOutput.vue';
import { isJobTerminal, jobStatusLabel, jobStatusTagType, jobTypeLabel, type JobItem } from './job';
import { fmtDateTime } from '../utils/format';

/**
 * 任务抽屉: 栈页操作(up/down/restart)提交后弹出,展示任务元信息 + 实时输出。
 * 打开期间每 2.5s 轮询任务状态,进入终态后停止轮询。
 */
const props = defineProps<{ show: boolean; jobId: string | null }>();
const emit = defineEmits<{ (e: 'update:show', value: boolean): void }>();

const job = ref<JobItem | null>(null);
const loading = ref(false);
const error = ref('');
let timer: ReturnType<typeof setInterval> | null = null;

function stopTimer(): void {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

async function loadJob(): Promise<void> {
  if (!props.jobId) return;
  loading.value = true;
  try {
    job.value = await api<JobItem>(`/jobs/${encodeURIComponent(props.jobId)}`);
    error.value = '';
    if (isJobTerminal(job.value.status)) stopTimer();
  } catch (e) {
    error.value = (e as Error).message;
    stopTimer();
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.show, props.jobId] as const,
  ([show, id]) => {
    stopTimer();
    if (show && id) {
      job.value = null;
      error.value = '';
      void loadJob();
      timer = setInterval(() => void loadJob(), 2500);
    }
  },
  { immediate: true }
);

onUnmounted(stopTimer);

function close(): void {
  emit('update:show', false);
}

const title = computed(() => (props.jobId ? `任务 ${props.jobId}` : '任务'));
</script>

<template>
  <n-drawer
    :show="show"
    :width="720"
    placement="right"
    :auto-focus="false"
    :close-on-esc="true"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <n-drawer-content :title="title" closable @close="close">
      <n-alert v-if="error" type="error" title="加载任务失败" style="margin-bottom: 12px">
        {{ error }}
      </n-alert>

      <n-spin :show="loading && !job">
        <n-descriptions
          v-if="job"
          bordered
          :column="2"
          label-placement="left"
          size="small"
          style="margin-bottom: 12px"
        >
          <n-descriptions-item label="类型">{{ jobTypeLabel(job.type) }}</n-descriptions-item>
          <n-descriptions-item label="栈">
            <span class="mono">{{ job.stack }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="状态">
            <n-tag size="small" :bordered="false" :type="jobStatusTagType(job.status)">
              {{ jobStatusLabel(job.status) }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="退出码">
            <span class="mono">{{ job.exit_code ?? '—' }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="创建时间">
            {{ fmtDateTime(job.created_at) }}
          </n-descriptions-item>
          <n-descriptions-item label="结束时间">
            {{ fmtDateTime(job.finished_at) }}
          </n-descriptions-item>
        </n-descriptions>

        <JobOutput v-if="job" :job="job" auto-live />
        <div v-else-if="!error" class="dim" style="padding: 24px 0; text-align: center">
          加载中…
        </div>
      </n-spin>
    </n-drawer-content>
  </n-drawer>
</template>
