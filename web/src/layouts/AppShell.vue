<script setup lang="ts">
import { computed, h } from 'vue';
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router';
import {
  NButton,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  type MenuOption,
} from 'naive-ui';
import { clearToken } from '../api/client';
import { message } from '../utils/feedback';
import { ICONS, type IconName } from '../utils/icons';

const route = useRoute();
const router = useRouter();

const APP_VERSION = __APP_VERSION__;

function menuLabel(to: string, label: string) {
  return () => h(RouterLink, { to, class: 'menu-link' }, { default: () => label });
}

function menuIcon(name: IconName) {
  return () => h('span', { class: 'menu-icon', innerHTML: ICONS[name] });
}

const menuOptions: MenuOption[] = [
  { key: '/dashboard', label: menuLabel('/dashboard', '总览'), icon: menuIcon('dashboard') },
  { key: '/containers', label: menuLabel('/containers', '容器'), icon: menuIcon('container') },
  { key: '/stacks', label: menuLabel('/stacks', 'Compose 栈'), icon: menuIcon('stacks') },
  { key: '/jobs', label: menuLabel('/jobs', '任务中心'), icon: menuIcon('jobs') },
  { key: '/keys', label: menuLabel('/keys', 'API Key'), icon: menuIcon('key') },
  { key: '/audit', label: menuLabel('/audit', '审计日志'), icon: menuIcon('audit') },
  { key: '/manual', label: menuLabel('/manual', '说明书'), icon: menuIcon('manual') },
  { key: '/settings', label: menuLabel('/settings', '设置'), icon: menuIcon('settings') },
];

const activeKey = computed(() =>
  route.path.startsWith('/containers') ? '/containers' : route.path
);

const pageTitle = computed(() => (route.meta.title ? String(route.meta.title) : ''));

function logout(): void {
  clearToken();
  message.success('已退出登录');
  void router.replace('/login');
}
</script>

<template>
  <n-layout class="shell" has-sider>
    <n-layout-sider :width="220" bordered content-style="background: #101116" class="shell-sider">
      <div class="brand">
        <span class="brand-dot"></span>
        <span class="brand-name">DockerAPI</span>
      </div>
      <n-menu
        :options="menuOptions"
        :value="activeKey"
        :inverted="false"
        content-style="background: #101116; padding-top: 8px"
      />
    </n-layout-sider>
    <n-layout class="shell-main">
      <n-layout-header bordered class="shell-header">
        <div class="shell-header-left">
          <span class="shell-title">DockerAPI</span>
          <span v-if="pageTitle" class="shell-page">{{ pageTitle }}</span>
        </div>
        <div class="shell-header-right">
          <span class="shell-ver dim">v{{ APP_VERSION }}</span>
          <n-button quaternary size="small" @click="logout">退出登录</n-button>
        </div>
      </n-layout-header>
      <n-layout-content class="shell-content" :native-scrollbar="false">
        <!-- :key 强制路由参数变化时重建组件(容器详情间切换会重载数据/轮询/SSE,审计 C10) -->
        <router-view :key="$route.fullPath" />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.shell {
  height: 100vh;
}

.shell-sider {
  background: #101116;
}

.brand {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid #23242c;
}

.brand-dot {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  background: linear-gradient(135deg, #8f9bff, #5b6ef5);
  flex-shrink: 0;
}

.brand-name {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.menu-link {
  text-decoration: none;
  color: inherit;
}

.menu-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.shell-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.shell-header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.shell-title {
  font-weight: 700;
  font-size: 15px;
}

.shell-page {
  color: rgba(255, 255, 255, 0.4);
  font-size: 13px;
}

.shell-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.shell-ver {
  font-size: 12px;
}

.shell-content {
  height: calc(100vh - 56px);
}
</style>
