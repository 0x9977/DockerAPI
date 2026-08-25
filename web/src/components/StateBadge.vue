<script setup lang="ts">
import { computed } from 'vue';
import { NTag } from 'naive-ui';

type TagType = 'success' | 'warning' | 'error' | 'info' | 'default';

const props = defineProps<{ state?: string | null }>();

const STATE_MAP: Record<string, TagType> = {
  running: 'success',
  paused: 'warning',
  exited: 'default',
  dead: 'error',
  created: 'info',
  restarting: 'info',
  removing: 'warning',
};

const tagType = computed<TagType>(() => STATE_MAP[props.state ?? ''] ?? 'default');
</script>

<template>
  <n-tag :type="tagType" size="small" :bordered="false" round>
    {{ state || '未知' }}
  </n-tag>
</template>
