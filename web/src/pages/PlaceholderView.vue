<script setup lang="ts">
import { useRoute } from 'vue-router';
import { NCard, NEmpty } from 'naive-ui';

const route = useRoute();
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ route.meta.title ?? '页面' }}</h2>
        <div class="page-subtitle">规划于第二阶段交付</div>
      </div>
    </div>
    <n-card>
      <n-empty size="large" description="建设中">
        <template #extra>
          <span class="dim">此功能将在第二阶段上线,当前为占位页面</span>
        </template>
      </n-empty>
    </n-card>
  </div>
</template>
