/** 与后端 docs/api.md 对齐的共享类型 */

export interface StatPoint {
  ts: string;
  cpu_percent: number;
  mem_mb: number;
  mem_limit_mb: number;
}

export interface ContainerItem {
  id: string;
  name: string;
  image: string;
  state: string;
  compose_project?: string | null;
  created?: string;
  is_self?: boolean;
  stats?: StatPoint[];
}

export interface LogLine {
  stream: string;
  text: string;
  ts?: string | null;
}

export interface ContainersSummary {
  running?: number | null;
  paused?: number | null;
  stopped?: number | null;
  all?: number | null;
}

export interface VersionInfo {
  panel?: string;
  docker?: string | null;
  api_version?: string;
  os?: string;
  storage_driver?: string;
  images_count?: number | null;
  volumes_count?: number | null;
  containers_summary?: ContainersSummary;
  docker_host?: string;
  /** daemon 不可达时后端仍返回 200,带 error 字段 */
  error?: string;
}
