<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { NButton, NCard, NForm, NFormItem, NInput } from 'naive-ui';
import { api, ApiError, getToken, probeVersion, setToken } from '../api/client';
import { message } from '../utils/feedback';

const router = useRouter();
const route = useRoute();

const username = ref('');
const password = ref('');
const loading = ref(false);

function redirectTarget(): string {
  const q = route.query.redirect;
  if (typeof q === 'string' && q.startsWith('/') && !q.startsWith('//')) return q;
  return '/dashboard';
}

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    message.warning('请输入用户名和密码');
    return;
  }
  loading.value = true;
  try {
    const r = await api<{ token: string; expires_at?: string }>('/auth/login', {
      method: 'POST',
      body: { username: username.value.trim(), password: password.value },
      skipAuth: true,
    });
    setToken(r.token);
    message.success('登录成功');
    void router.replace(redirectTarget());
  } catch (e) {
    const err = e as ApiError;
    if (err.status === 503 && err.code === 'setup_required') {
      void router.replace('/setup');
    } else if (err.status === 429) {
      message.error('尝试过于频繁,请稍后再试');
    } else if (err.status === 401) {
      message.error('用户名或密码错误');
    } else {
      message.error(err.message);
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (getToken()) return;
  const r = await probeVersion();
  if (r === 'setup_required') void router.replace('/setup');
});
</script>

<template>
  <div class="auth-wrap">
    <n-card class="auth-card" :bordered="true">
      <div class="auth-brand">
        <span class="auth-dot"></span>
        <span class="auth-title">DockerAPI</span>
      </div>
      <div class="auth-sub dim">登录容器管理面板</div>
      <n-form label-placement="top" @keyup.enter="submit">
        <n-form-item label="用户名">
          <n-input v-model:value="username" placeholder="用户名" :disabled="loading" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            placeholder="密码"
            :disabled="loading"
          />
        </n-form-item>
        <n-button type="primary" block :loading="loading" @click="submit">登 录</n-button>
      </n-form>
    </n-card>
  </div>
</template>

<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(1200px 600px at 20% -10%, #1b2035 0%, #0e0f13 55%);
}

.auth-card {
  width: 380px;
  border-radius: 10px;
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.auth-dot {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  background: linear-gradient(135deg, #8f9bff, #5b6ef5);
}

.auth-title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.auth-sub {
  font-size: 13px;
  margin-bottom: 18px;
}
</style>
