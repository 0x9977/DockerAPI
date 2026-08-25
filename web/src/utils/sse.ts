import { fetchEventSource, type EventSourceMessage } from '@microsoft/fetch-event-source';
import { API_BASE, getToken, redirectToLogin, redirectToSetup } from '../api/client';

export type SseState = 'connecting' | 'open' | 'retrying' | 'closed' | 'failed';

export interface SseHandlers {
  onMessage: (msg: EventSourceMessage) => void;
  /** 服务端发送 event: end 时触发(容器停止/删除等导致流结束) */
  onEnd?: (data: string | null) => void;
  /** 服务端发送 event: error 时触发(404/429/daemon 故障等业务性致命错误,不再重连) */
  onErrorEvent?: (data: string | null) => void;
  onStateChange?: (state: SseState) => void;
}

class FatalSseError extends Error {}

const MAX_ATTEMPTS = 10; // 重连上限(审计 C11): 超过后置为 failed,不再无限重试

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function buildAuthHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function sseUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/**
 * 基于 @microsoft/fetch-event-source 的 SSE 订阅。
 * - 携带 Authorization 头(原生 EventSource 做不到,且禁止 ?token= 传参)
 * - 断线重连:指数退避 1s 起步,上限 30s,最多 10 次;成功打开后退避重置
 * - 401 → 跳登录,503 → 跳引导页(致命错误,不重连)
 * - 服务端 event: end → 正常结束,不重连
 * - 服务端 event: error → 业务性错误(如容器不存在/订阅超限),不重连
 */
export function createSse(url: string, handlers: SseHandlers): { stop: () => void } {
  let stopped = false;
  let controller: AbortController | null = null;

  async function run(): Promise<void> {
    let attempt = 0;
    while (!stopped) {
      let ended = false;
      controller = new AbortController();
      try {
        handlers.onStateChange?.('connecting');
        await fetchEventSource(url, {
          signal: controller.signal,
          headers: buildAuthHeaders(),
          openWhenHidden: true,
          async onopen(res) {
            if (res.ok && (res.headers.get('content-type') ?? '').includes('text/event-stream')) {
              attempt = 0;
              handlers.onStateChange?.('open');
              return;
            }
            if (res.status === 401) {
              void redirectToLogin();
              throw new FatalSseError(`SSE 未授权 (HTTP ${res.status})`);
            }
            if (res.status === 503) {
              void redirectToSetup();
              throw new FatalSseError('服务处于初始化模式');
            }
            throw new FatalSseError(`SSE 连接失败 (HTTP ${res.status})`);
          },
          onmessage(msg: EventSourceMessage) {
            if (msg.event === 'end') {
              ended = true;
              handlers.onEnd?.(msg.data);
            } else if (msg.event === 'error') {
              // 服务端业务性错误(路由层把生成器内 ApiError 转为首帧 error 事件)
              ended = true;
              handlers.onErrorEvent?.(msg.data);
            } else {
              handlers.onMessage(msg);
            }
          },
          onerror(err: unknown) {
            // 抛出以阻止库内建重试,由本函数外层循环统一控制退避
            throw err;
          },
        });
        if (ended || stopped) {
          handlers.onStateChange?.('closed');
          return;
        }
        // 流被服务端正常关闭但未发 end → 视为临时断开,走重连
      } catch (err) {
        if (stopped || controller.signal.aborted) return;
        if (err instanceof FatalSseError) {
          handlers.onStateChange?.('closed');
          return;
        }
        // 网络错误等临时故障 → 指数退避重连
      }

      if (stopped) return;
      if (attempt >= MAX_ATTEMPTS) {
        handlers.onStateChange?.('failed');
        return;
      }
      const delay = Math.min(1000 * 2 ** attempt, 30000);
      attempt += 1;
      handlers.onStateChange?.('retrying');
      await sleep(delay);
    }
  }

  void run();

  return {
    stop() {
      stopped = true;
      controller?.abort();
      handlers.onStateChange?.('closed');
    },
  };
}
