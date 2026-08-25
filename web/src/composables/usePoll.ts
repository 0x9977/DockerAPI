import { onBeforeUnmount, onMounted } from 'vue';

export interface PollHandle {
  refresh: () => Promise<void>;
}

/**
 * 定时轮询,页面不可见时暂停(visibilitychange),恢复可见时立即刷一次。
 * fn 内部需自行捕获错误(避免轮询中断)。
 */
export function usePoll(fn: () => Promise<unknown>, intervalMs = 5000): PollHandle {
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh(): Promise<void> {
    try {
      await fn();
    } catch {
      /* 调用方在 fn 内处理错误 */
    }
  }

  function start(): void {
    if (timer === null && !document.hidden) {
      timer = setInterval(() => void refresh(), intervalMs);
    }
  }

  function stop(): void {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  function onVisibility(): void {
    if (document.hidden) {
      stop();
    } else {
      start();
      void refresh();
    }
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility);
    void refresh();
    start();
  });

  onBeforeUnmount(() => {
    stop();
    document.removeEventListener('visibilitychange', onVisibility);
  });

  return { refresh };
}
