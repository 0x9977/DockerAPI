/** 长任务(stack compose 操作)共享类型与展示映射,栈页与任务中心共用 */

export interface JobItem {
  id: string;
  type: string; // stack.up | stack.down | stack.restart
  stack: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'timeout';
  exit_code: number | null;
  output?: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export type TagType = 'success' | 'warning' | 'error' | 'info' | 'default';

export function isJobTerminal(status?: string | null): boolean {
  return status === 'done' || status === 'failed' || status === 'timeout';
}

export function jobStatusTagType(status?: string | null): TagType {
  switch (status) {
    case 'done':
      return 'success';
    case 'running':
      return 'warning';
    case 'failed':
    case 'timeout':
      return 'error';
    case 'queued':
      return 'info';
    default:
      return 'default';
  }
}

export function jobStatusLabel(status?: string | null): string {
  switch (status) {
    case 'queued':
      return '排队中';
    case 'running':
      return '运行中';
    case 'done':
      return '已完成';
    case 'failed':
      return '失败';
    case 'timeout':
      return '超时';
    default:
      return status ?? '未知';
  }
}

export function jobTypeLabel(type?: string | null): string {
  switch (type) {
    case 'stack.up':
      return '栈启动 (up)';
    case 'stack.down':
      return '栈停止 (down)';
    case 'stack.restart':
      return '栈重启 (restart)';
    default:
      return type ?? '—';
  }
}
