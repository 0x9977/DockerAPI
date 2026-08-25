/** 侧边栏菜单的内联 SVG 图标(描边风格,currentColor 继承) */

const svg = (paths: string): string =>
  `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

export const ICONS = {
  dashboard: svg(
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
  ),
  container: svg(
    '<path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z"/><path d="M3 7.5v9l9 4.5 9-4.5v-9"/><path d="M12 12v9"/>'
  ),
  stacks: svg(
    '<path d="m12 3 9 4.5-9 4.5-9-4.5L12 3Z"/><path d="m3 12.5 9 4.5 9-4.5"/><path d="m3 17 9 4.5 9-4.5"/>'
  ),
  jobs: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>'),
  key: svg('<circle cx="8" cy="16" r="4"/><path d="m11 13 8.5-8.5"/><path d="m16.5 5 2.5 2.5"/>'),
  audit: svg('<path d="M12 3 5 6v5c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3Z"/>'),
  settings: svg(
    '<path d="M4 7h9"/><circle cx="16" cy="7" r="2.5"/><path d="M4 17h3"/><circle cx="10.5" cy="17" r="2.5"/><path d="M13 17h7"/>'
  ),
  manual: svg(
    '<path d="M4 19.5V5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5 2.5 2.5 0 0 0 6.5 23H20"/><path d="M9 8h7"/>'
  ),
} as const;

export type IconName = keyof typeof ICONS;
