<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { NAlert, NButton, NCard, NForm, NFormItem, NInput } from 'naive-ui';
import { api, ApiError, setToken } from '../api/client';
import { message } from '../utils/feedback';

const router = useRouter();

const username = ref('');
const password = ref('');
const confirmPassword = ref('');
const loading = ref(false);

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    message.warning('请输入用户名和密码');
    return;
  }
  if (password.value.length < 8) {
    message.warning('密码至少 8 位');
    return;
  }
  if (password.value !== confirmPassword.value) {
    message.warning('两次输入的密码不一致');
    return;
  }
  loading.value = true;
  try {
    const r = await api<{ token: string }>('/auth/setup', {
      method: 'POST',
      body: { username: username.value.trim(), password: password.value },
      skipAuth: true,
    });
    setToken(r.token);
    message.success('初始化成功,已自动登录');
    void router.replace('/dashboard');
  } catch (e) {
    const err = e as ApiError;
    if (err.status === 409) {
      message.error('系统已完成初始化,请直接登录');
      void router.replace('/login');
    } else if (err.status === 422) {
      message.error('输入不合法:用户名/密码不符合要求');
    } else {
      message.error(err.message);
    }
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="auth-wrap">
    <n-card class="auth-card" :bordered="true">
      <div class="auth-brand">
        <span class="auth-dot"></span>
        <span class="auth-title">DockerAPI</span>
      </div>
      <div class="auth-sub dim">首次初始化</div>
      <n-alert type="info" :bordered="false" style="margin-bottom: 16px">
        检测到系统尚未初始化,请创建管理员账户。此操作仅需执行一次。
      </n-alert>
      <n-form label-placement="top" @keyup.enter="submit">
        <n-form-item label="管理员用户名">
          <n-input v-model:value="username" placeholder="用户名" :disabled="loading" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位"
            :disabled="loading"
          />
        </n-form-item>
        <n-form-item label="确认密码">
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            placeholder="再次输入密码"
            :disabled="loading"
          />
        </n-form-item>
        <n-button type="primary" block :loading="loading" @click="submit">创建管理员并进入</n-button>
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
