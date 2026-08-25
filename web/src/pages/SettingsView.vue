<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  NAlert,
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NForm,
  NFormItem,
  NInput,
  NSpin,
  NTag,
} from 'naive-ui';
import { api, ApiError } from '../api/client';
import { message } from '../utils/feedback';
import type { VersionInfo } from '../types';

const APP_VERSION = __APP_VERSION__;

/* ---------------- 当前主体 ---------------- */

interface MeInfo {
  type: string; // user | api_key
  name: string;
  scopes: string[];
}

const me = ref<MeInfo | null>(null);
const meError = ref('');
const meLoading = ref(true);

async function loadMe(): Promise<void> {
  try {
    me.value = await api<MeInfo>('/auth/me');
    meError.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) meError.value = err.message;
  } finally {
    meLoading.value = false;
  }
}

const isUser = computed(() => me.value?.type === 'user');

/* ---------------- 修改密码 ---------------- */

const oldPassword = ref('');
const newPassword = ref('');
const confirmPassword = ref('');
const pwBusy = ref(false);

async function submitPassword(): Promise<void> {
  if (!oldPassword.value) {
    message.warning('请输入旧密码');
    return;
  }
  if (newPassword.value.length < 8) {
    message.warning('新密码至少 8 位');
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    message.warning('两次输入的新密码不一致');
    return;
  }
  pwBusy.value = true;
  try {
    await api('/auth/password', {
      method: 'PATCH',
      body: { old_password: oldPassword.value, new_password: newPassword.value },
    });
    message.success('密码已修改,当前登录保持有效');
    oldPassword.value = '';
    newPassword.value = '';
    confirmPassword.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status === 400 && err.code === 'invalid_password') {
      message.error('旧密码不正确');
    } else if (err.status === 403) {
      message.warning('请用管理员账号登录操作');
    } else {
      message.error(err.message);
    }
  } finally {
    pwBusy.value = false;
  }
}

/* ---------------- 系统信息 ---------------- */

const version = ref<VersionInfo | null>(null);
const versionError = ref('');
const versionLoading = ref(true);

async function loadVersion(): Promise<void> {
  try {
    version.value = await api<VersionInfo>('/version');
    versionError.value = '';
  } catch (e) {
    const err = e as ApiError;
    if (err.status !== 401 && err.status !== 503) versionError.value = err.message;
  } finally {
    versionLoading.value = false;
  }
}

onMounted(() => {
  void loadMe();
  void loadVersion();
});

const versionMismatch = computed(() => {
  const p = version.value?.panel;
  return !!p && p !== APP_VERSION;
});
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">设置</h2>
        <div class="page-subtitle">当前主体、密码与系统信息</div>
      </div>
    </div>

    <!-- 当前主体 -->
    <n-card size="small" title="当前主体" style="margin-bottom: 14px">
      <n-spin :show="meLoading">
        <n-alert v-if="meError" type="error" style="margin-bottom: 10px">{{ meError }}</n-alert>
        <template v-if="me">
          <n-descriptions bordered :column="3" label-placement="left" size="small">
            <n-descriptions-item label="类型">
              <n-tag
                size="small"
                :bordered="false"
                :type="me.type === 'user' ? 'info' : 'warning'"
              >
                {{ me.type === 'user' ? '用户(JWT)' : 'API Key' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="名称">
              <span class="mono">{{ me.name }}</span>
            </n-descriptions-item>
            <n-descriptions-item label="Scopes">
              <span class="scope-tags">
                <n-tag
                  v-for="s in me.scopes"
                  :key="s"
                  size="small"
                  :bordered="false"
                  :type="s === 'admin' ? 'warning' : 'info'"
                >
                  {{ s }}
                </n-tag>
              </span>
            </n-descriptions-item>
          </n-descriptions>
        </template>
      </n-spin>
    </n-card>

    <div class="settings-grid">
      <!-- 修改密码 -->
      <n-card size="small" title="修改密码" class="grid-card">
        <n-alert
          v-if="me && !isUser"
          type="info"
          style="margin-bottom: 12px"
        >
          当前为 API Key 会话,修改密码请用管理员账号登录后操作
        </n-alert>
        <n-form label-placement="top" :show-feedback="false" class="pw-form">
          <n-form-item label="旧密码">
            <n-input
              v-model:value="oldPassword"
              type="password"
              show-password-on="click"
              placeholder="当前密码"
              :disabled="!!me && !isUser"
            />
          </n-form-item>
          <n-form-item label="新密码(至少 8 位)">
            <n-input
              v-model:value="newPassword"
              type="password"
              show-password-on="click"
              placeholder="至少 8 位"
              :disabled="!!me && !isUser"
            />
          </n-form-item>
          <n-form-item label="确认新密码">
            <n-input
              v-model:value="confirmPassword"
              type="password"
              show-password-on="click"
              placeholder="再次输入新密码"
              :disabled="!!me && !isUser"
              @keyup.enter="submitPassword"
            />
          </n-form-item>
          <div class="dim pw-hint">
            修改后当前登录保持有效;v1 不吊销已签发的 JWT,旧 token 将在自然过期(24h)后失效
          </div>
          <n-button
            type="primary"
            style="margin-top: 14px"
            :loading="pwBusy"
            :disabled="!!me && !isUser"
            @click="submitPassword"
          >
            修改密码
          </n-button>
        </n-form>
      </n-card>

      <!-- 系统信息 -->
      <n-card size="small" title="系统信息" class="grid-card">
        <template #header-extra>
          <span class="dim ver-hint">前端构建版本 v{{ APP_VERSION }}</span>
        </template>
        <n-spin :show="versionLoading">
          <n-alert v-if="versionError" type="error" style="margin-bottom: 10px">
            {{ versionError }}
          </n-alert>
          <n-alert
            v-if="versionMismatch"
            type="warning"
            title="版本不一致"
            style="margin-bottom: 10px"
          >
            前端构建版本 v{{ APP_VERSION }} 与面板版本 v{{ version?.panel }} 不一致,页面资源可能过期,建议强制刷新(Ctrl+F5)
          </n-alert>
          <n-alert
            v-else-if="version?.error"
            type="warning"
            title="Docker daemon 不可达"
            style="margin-bottom: 10px"
          >
            {{ version.error }}
          </n-alert>
          <n-descriptions bordered :column="1" label-placement="left" size="small">
            <n-descriptions-item label="面板版本">
              <span class="mono">{{ version?.panel ?? '—' }}</span>
            </n-descriptions-item>
            <n-descriptions-item label="Docker 版本">
              <span class="mono">
                {{ version?.docker ?? '—' }}{{ version?.api_version ? ` (API ${version.api_version})` : '' }}
              </span>
            </n-descriptions-item>
            <n-descriptions-item label="操作系统">
              {{ version?.os ?? '—' }}
            </n-descriptions-item>
            <n-descriptions-item label="存储驱动">
              {{ version?.storage_driver ?? '—' }}
            </n-descriptions-item>
            <n-descriptions-item label="Docker 连接(docker_host)">
              <span class="mono">{{ version?.docker_host ?? '—' }}</span>
            </n-descriptions-item>
          </n-descriptions>
        </n-spin>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 14px;
  align-items: start;
}

.grid-card {
  min-width: 0;
}

.scope-tags {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pw-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pw-hint {
  font-size: 12px;
  line-height: 1.6;
}

.ver-hint {
  font-size: 12px;
}
</style>
