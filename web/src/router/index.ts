import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { getToken, probeVersion } from '../api/client';
import AppShell from '../layouts/AppShell.vue';

declare module 'vue-router' {
  interface RouteMeta {
    title?: string;
    public?: boolean;
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    component: () => import('../pages/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/setup',
    component: () => import('../pages/SetupView.vue'),
    meta: { public: true, title: '初始化' },
  },
  {
    path: '/',
    component: AppShell,
    children: [
      { path: '', redirect: '/dashboard' },
      {
        path: 'dashboard',
        component: () => import('../pages/DashboardView.vue'),
        meta: { title: '总览' },
      },
      {
        path: 'containers',
        component: () => import('../pages/ContainersView.vue'),
        meta: { title: '容器' },
      },
      {
        path: 'containers/:id',
        component: () => import('../pages/ContainerDetailView.vue'),
        meta: { title: '容器详情' },
      },
      {
        path: 'stacks',
        component: () => import('../pages/StacksView.vue'),
        meta: { title: 'Compose 栈' },
      },
      {
        path: 'jobs',
        component: () => import('../pages/JobsView.vue'),
        meta: { title: '任务中心' },
      },
      {
        path: 'keys',
        component: () => import('../pages/KeysView.vue'),
        meta: { title: 'API Key' },
      },
      {
        path: 'audit',
        component: () => import('../pages/AuditView.vue'),
        meta: { title: '审计日志' },
      },
      {
        path: 'manual',
        component: () => import('../pages/ManualView.vue'),
        meta: { title: '说明书' },
      },
      {
        path: 'settings',
        component: () => import('../pages/SettingsView.vue'),
        meta: { title: '设置' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  if (to.meta.title) {
    document.title = `${to.meta.title} · DockerAPI`;
  } else {
    document.title = 'DockerAPI';
  }

  if (to.meta.public) return true;
  if (getToken()) return true;

  // 无 token:先试探 /api/v1/version,503 → 引导页,401/403 → 登录页
  const probe = await probeVersion();
  if (probe === 'setup_required') {
    return { path: '/setup', replace: true };
  }
  return {
    path: '/login',
    query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    replace: true,
  };
});

export default router;
