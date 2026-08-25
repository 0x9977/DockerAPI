/**
 * 统一 fetch 封装。
 *
 * - 自动携带 Bearer token(localStorage "dapi_token")
 * - 401 → 清 token 跳 /login;503 setup_required → 跳 /setup
 * - 错误信封 {error:{code,message}} 解析为 ApiError 抛出
 */

export const API_BASE = '/api/v1';
export const TOKEN_KEY = 'dapi_token';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export type ApiQueryValue = string | number | boolean | undefined | null;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, ApiQueryValue>;
  signal?: AbortSignal;
  /** 登录/setup 等豁免端点不携带 token */
  skipAuth?: boolean;
  /** 探测场景:不触发 401/503 自动跳转 */
  skipIntercept?: boolean;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** 动态引入 router,避免 client ↔ router 静态循环依赖 */
async function nav(path: string): Promise<void> {
  const mod = await import('../router');
  const router = mod.default;
  if (router.currentRoute.value.path !== path) {
    void router.replace(path);
  }
}

export async function redirectToLogin(): Promise<void> {
  clearToken();
  await nav('/login');
}

export async function redirectToSetup(): Promise<void> {
  clearToken();
  await nav('/setup');
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = {};
  const token = getToken();
  if (!opts.skipAuth && token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';

  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch {
    throw new ApiError(0, 'network_error', '无法连接服务器,请检查网络或服务状态');
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const envelope = (data ?? null) as ErrorEnvelope | null;
    const code = envelope?.error?.code ?? `http_${res.status}`;
    const msg = envelope?.error?.message ?? `请求失败 (HTTP ${res.status})`;
    if (!opts.skipIntercept) {
      if (res.status === 401) {
        await redirectToLogin();
      } else if (res.status === 503 && code === 'setup_required') {
        await redirectToSetup();
      }
    }
    throw new ApiError(res.status, code, msg);
  }

  return data as T;
}

export type ProbeResult = 'ok' | 'unauthorized' | 'setup_required' | 'network_error';

/**
 * 应用启动探测: GET /api/v1/version
 * 401/403 → 登录页,503 setup_required → 引导页。
 */
export async function probeVersion(): Promise<ProbeResult> {
  try {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}/version`, { headers });
    if (res.status === 503) {
      const text = await res.text();
      try {
        const data = JSON.parse(text) as ErrorEnvelope;
        if (data?.error?.code === 'setup_required') return 'setup_required';
      } catch {
        /* 非 JSON 响应,按未授权处理 */
      }
      return 'unauthorized';
    }
    if (res.status === 401 || res.status === 403) return 'unauthorized';
    if (res.ok) return 'ok';
    return 'unauthorized';
  } catch {
    return 'network_error';
  }
}
