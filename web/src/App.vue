<script setup lang="ts">
import { NConfigProvider, darkTheme, dateZhCN, zhCN } from 'naive-ui';
import { themeOverrides } from './theme';
</script>

<template>
  <n-config-provider
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <router-view />
  </n-config-provider>
</template>
