import { createDiscreteApi, darkTheme, dateZhCN, zhCN } from 'naive-ui';
import { themeOverrides } from '../theme';

/**
 * 离散 API:在组件外(如 api 封装、页面逻辑)也能弹 message/dialog。
 * 主题与应用保持一致(暗色)。
 */
const discrete = createDiscreteApi(['message', 'dialog'], {
  configProviderProps: {
    theme: darkTheme,
    themeOverrides,
    locale: zhCN,
    dateLocale: dateZhCN,
  },
});

export const message = discrete.message;
export const dialog = discrete.dialog;
