/**
 * antd-theme.ts —— Ant Design 主题令牌映射（依据技术文档 6.2「设计令牌落地」）
 *
 * 功能说明：
 *   AntD 的 ConfigProvider 不支持 colorPrimary: 'var(--primary)' 这类 CSS 变量写法，
 *   因此这里注入与 tokens.css **同源**的实测色值，仅用于 Modal / DatePicker / Pagination
 *   三类复杂交互件（用户决策②：其余组件一律自研）。
 *
 * 维护纪律：
 *   本文件的色值必须与 src/styles/tokens.css 保持一致；修改令牌时两处同步，
 *   否则会出现 AntD 组件与自研组件配色不一致的问题。
 *
 * 用法：
 *   import { antdTheme } from '../antd-theme';
 *   <ConfigProvider theme={antdTheme}>...</ConfigProvider>
 */

import type { ThemeConfig } from 'antd';

/** AntD 主题配置（token 值与 tokens.css 4.1 / 4.3 对应） */
export const antdTheme: ThemeConfig = {
  token: {
    // 色彩
    colorPrimary: '#1E6FE8', // --primary
    colorInfo: '#1E6FE8',
    colorSuccess: '#00A86E', // --accent-green
    colorError: '#C62828', // --semantic-up / --status-warn
    colorWarning: '#C62828',
    colorText: '#0E2A4E', // --text-1
    colorTextSecondary: '#2C4A73', // --text-2
    colorTextTertiary: '#4E6E96', // --text-3
    colorBorder: '#D3E0F1', // --border
    colorBorderSecondary: '#E4EDF8', // --divider
    colorBgContainer: '#FFFFFF', // --surface
    colorBgLayout: '#F3F7FD', // --bg

    // 字体
    fontFamily: "Inter, 'Hanken Grotesk', sans-serif", // --ff-body
    fontSize: 14, // --fs-card-desc（AntD 组件基准字号）

    // 圆角
    borderRadius: 12, // --r-xs
    borderRadiusLG: 20, // --r-md

    // 阴影
    boxShadow: '0 4px 24px rgba(30, 111, 232, .12)', // --shadow-glow
  },
};

export default antdTheme;
