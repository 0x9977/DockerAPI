<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { NSwitch, NTag } from 'naive-ui';
import LogViewer from './LogViewer.vue';
import type { JobItem } from './job';
import { isJobTerminal } from './job';
import type { LogLine } from '../types';
import { createSse, sseUrl, type SseState } from '../utils/sse';

/**
 * 单个任务的输出视图: 默认展示 Job.output 快照;
 * 打开"实时"后订阅 /jobs/{id}/stream(服务端会先重放全量输出再增量推送),
 * end 事件到达或任务进入终态时自动停止。
 */
const props = withDefaults(defineProps<{ job: JobItem; autoLive?: boolean }>(), {
  autoLive: false,
});

const live = ref(false);
const liveState = ref<SseState>('closed');
/** null = 未在流式接收,回退展示 props.job.output */
const streamText = ref<string | null>(null);
let sseHandle: { stop: () => void } | null = null;

const text = computed(() => streamText.value ?? props.job.output ?? '');

const lines = computed<LogLine[]>(() =>
  text.value.split('\n').map((l) => ({ stream: 'stdout', text: l }))
);

function startLive(): void {
  // 纯拆流,不动 live 开关状态(否则会再次触发 watch 拆掉新建的流)
  teardownStream();
  streamText.value = '';
  liveState.value = 'connecting';
  sseHandle = createSse(sseUrl(`/jobs/${encodeURIComponent(props.job.id)}/stream`), {
    onMessage(msg) {
      try {
        const d = JSON.parse(msg.data) as { chunk?: string };
        if (typeof d.chunk === 'string') streamText.value += d.chunk;
      } catch {
        /* 忽略无法解析的帧 */
      }
    },
    onEnd() {
      stopLive();
    },
    onStateChange(s) {
      liveState.value = s;
    },
  });
}

function teardownStream(): void {
  sseHandle?.stop();
  sseHandle = null;
  // 流未收到任何内容就被关闭(如连接失败)→ 回退到任务快照输出
  if (streamText.value === '') streamText.value = null;
  liveState.value = 'closed';
}

function stopLive(): void {
  teardownStream();
  live.value = false;
}

watch(live, (on) => {
  if (on) startLive();
  else stopLive();
});

// 流被服务端/网络侧关闭(SSE 致命错误未走 onEnd)→ 同步开关状态
watch(liveState, (s) => {
  if (s === 'closed' && live.value) live.value = false;
});

// 列表轮询刷新 job 后,任务进入终态 → 停止流
watch(
  () => props.job.status,
  (s) => {
    if (isJobTerminal(s) && live.value) stopLive();
  }
);

onMounted(() => {
  if (props.autoLive || !isJobTerminal(props.job.status)) live.value = true;
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
</script>

<template>
  <div class="job-output">
    <div class="job-output-toolbar">
      <div class="job-output-toolbar-left">
        <span class="dim">实时</span>
        <n-switch v-model:value="live" size="small" />
        <n-tag
          v-if="live || liveState !== 'closed'"
          size="small"
          :bordered="false"
          :type="liveTagType"
        >
          {{ liveStateText }}
        </n-tag>
      </div>
      <span class="dim job-output-hint">
        开启后订阅任务流:先重放历史输出,再实时追加;任务结束自动断开
      </span>
    </div>
    <LogViewer :lines="lines" height="320px" placeholder="暂无输出" />
  </div>
</template>

<style scoped>
.job-output {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.job-output-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.job-output-toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.job-output-hint {
  font-size: 12px;
}
</style>
