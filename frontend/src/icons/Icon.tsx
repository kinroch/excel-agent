/**
 * Icon.tsx —— 图标精灵组件（依据技术文档 6.1「Lucide 风格 SVG 精灵，禁 emoji」）
 *
 * 功能说明：
 *   1. 以 24×24 视口、1.8 描边的内联 SVG 提供全站图标，禁用 emoji（UI/UX 5.8）；
 *   2. 图标路径集中在本文件维护，新增图标需同时登记到 ICON_PATHS；
 *   3. 纯图标按钮必须由调用方传入 aria-label（UI/UX 5 章交互纪律），本组件透传；
 *   4. 描边使用 currentColor，随父级文字颜色变化，天然适配令牌体系。
 *
 * 用法：
 *   <Icon name="chat" size={20} />
 *   <button aria-label="上传文件"><Icon name="upload" /></button>
 */

import type { CSSProperties, ReactElement } from 'react';

/** 已登记的图标名（新增图标时同步扩充） */
export type IconName =
  | 'workspace'
  | 'chat'
  | 'report'
  | 'sessions'
  | 'stats'
  | 'dashboard'
  | 'users'
  | 'logout'
  | 'upload'
  | 'arrow-right';

/** 图标路径表：每项为一组 SVG 子元素 */
const ICON_PATHS: Record<IconName, ReactElement> = {
  // 数据工作台：网格
  workspace: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  // 对话式分析：消息气泡
  chat: (
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  ),
  // 分析报告：文档
  report: (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M8 13h8M8 17h5" />
    </>
  ),
  // 会话管理：列表
  sessions: (
    <>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="M3 6h.01M3 12h.01M3 18h.01" />
    </>
  ),
  // 个人统计：柱状图
  stats: (
    <>
      <path d="M3 3v18h18" />
      <rect x="7" y="11" width="3" height="7" rx="1" />
      <rect x="13" y="7" width="3" height="11" rx="1" />
    </>
  ),
  // 全局看板：仪表盘
  dashboard: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 12l4-3" />
    </>
  ),
  // 用户管理：人群
  users: (
    <>
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
    </>
  ),
  // 登出
  logout: (
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </>
  ),
  // 上传
  upload: (
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M17 8l-5-5-5 5M12 3v12" />
    </>
  ),
  // 箭头
  'arrow-right': <path d="M5 12h14M13 6l6 6-6 6" />,
};

/** Icon 组件属性 */
export interface IconProps {
  /** 图标名（须已在 ICON_PATHS 登记） */
  name: IconName;
  /** 尺寸（px），默认 20 */
  size?: number;
  /** 描边宽度，默认 1.8（Lucide 风格基准） */
  strokeWidth?: number;
  /** 自定义类名 */
  className?: string;
  /** 自定义内联样式 */
  style?: CSSProperties;
}

/**
 * 渲染一个内联 SVG 图标。
 *
 * @param props IconProps
 * @returns SVG 元素；currentColor 描边，随父级文字色变化
 */
export function Icon({ name, size = 20, strokeWidth = 1.8, className, style }: IconProps): ReactElement {
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

export default Icon;
