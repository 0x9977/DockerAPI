<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { NButton, NInput, NTag } from 'naive-ui';
import type { LogLine } from '../types';

const props = withDefaults(
  defineProps<{
    lines: LogLine[];
    height?: string;
    placeholder?: string;
  }>(),
  { height: '420px', placeholder: '暂无日志' }
);

const filter = ref('');
const boxRef = ref<HTMLElement | null>(null);
const follow = ref(true);

const filtered = computed<LogLine[]>(() => {
  const q = filter.value.trim().toLowerCase();
  if (!q) return props.lines;
  return props.lines.filter((l) => l.text.toLowerCase().includes(q));
});

function nearBottom(el: HTMLElement): boolean {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
}

function onScroll(): void {
  const el = boxRef.value;
  if (!el) return;
  follow.value = nearBottom(el);
}

function scrollBottom(): void {
  const el = boxRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
  follow.value = true;
}

async function stickToBottom(): Promise<void> {
  await nextTick();
  const el = boxRef.value;
  if (el) el.scrollTop = el.scrollHeight;
}

// 新行到达时自动滚底(上滚后暂停跟随)
watch(
  () => props.lines.length,
  () => {
    if (follow.value) void stickToBottom();
  }
);

watch(filter, () => {
  if (follow.value) void stickToBottom();
});
</script>

<template>
  <div class="logviewer">
    <div class="logviewer-toolbar">
      <n-input
        v-model:value="filter"
        size="small"
        clearable
        placeholder="过滤日志(客户端)"
        class="logviewer-filter"
      />
      <n-tag size="small" :bordered="false" :type="follow ? 'success' : 'warning'">
        {{ follow ? '跟随中' : '已暂停跟随' }}
      </n-tag>
      <n-button size="tiny" quaternary @click="scrollBottom">回到底部</n-button>
      <span class="dim logviewer-count">{{ filtered.length }} 行</span>
    </div>
    <div ref="boxRef" class="logviewer-box" :style="{ height }" @scroll="onScroll">
      <div v-if="filtered.length === 0" class="logviewer-empty">{{ placeholder }}</div>
      <div
        v-for="(line, i) in filtered"
        :key="i"
        class="logviewer-line"
        :class="{ 'logviewer-line--stderr': line.stream === 'stderr' }"
      >
        <span v-if="line.ts" class="logviewer-ts">{{ line.ts }}</span>
        <span class="logviewer-text">{{ line.text }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.logviewer {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.logviewer-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.logviewer-filter {
  max-width: 260px;
}

.logviewer-count {
  font-size: 12px;
}

.logviewer-box {
  overflow: auto;
  background: #0a0b0e;
  border: 1px solid #262833;
  border-radius: 6px;
  padding: 8px 10px;
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.55;
}

.logviewer-line {
  white-space: pre-wrap;
  word-break: break-all;
  color: #c9ced9;
}

.logviewer-line--stderr {
  color: #ff7a7a;
}

.logviewer-ts {
  color: #5c6270;
  margin-right: 8px;
}

.logviewer-empty {
  color: #5c6270;
  padding: 24px 0;
  text-align: center;
}
</style>
