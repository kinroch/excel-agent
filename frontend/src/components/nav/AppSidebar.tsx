/**
 * AppSidebar.tsx —— 应用侧栏导航（依据 UI/UX 5.1 导航类组件 / 技术文档 6.4 组件清单）
 *
 * 功能说明：
 *   1. 渲染品牌区 + 导航项列表，导航项与 react-router 的 NavLink 绑定；
 *   2. 激活态使用品牌渐变文字裁切（UI/UX 4.1 --gradient-text）；
 *   3. hover 只改变背景与文字色，不引起布局位移（UI/UX 5 章交互纪律）；
 *   4. 用户端与管理端共用本组件，仅导航项不同。
 *
 * 用法：
 *   <AppSidebar items={[{ to: '/workspace', label: '数据工作台', icon: 'workspace' }]} />
 */

import type { ReactElement } from 'react';
import { NavLink } from 'react-router-dom';

import { Icon, type IconName } from '@/icons/Icon';

import './AppSidebar.css';

/** 单个导航项定义 */
export interface NavItemDef {
  /** 目标路由 */
  to: string;
  /** 显示文案 */
  label: string;
  /** 图标名 */
  icon: IconName;
}

/** AppSidebar 属性 */
export interface AppSidebarProps {
  /** 导航项列表 */
  items: NavItemDef[];
  /** 端标识：'admin' 时使用管理端侧栏宽度与标题 */
  side?: 'user' | 'admin';
  /** 品牌区副标题（区分两端） */
  brandSubtitle?: string;
}

/**
 * 侧栏导航组件。
 *
 * @param props AppSidebarProps
 * @returns 侧栏 JSX
 */
export function AppSidebar({ items, side = 'user', brandSubtitle }: AppSidebarProps): ReactElement {
  // 侧栏宽度由修饰类切换（用户端 220px / 管理端 220px，见 tokens.css）
  const sidebarClass = `app-sidebar${side === 'admin' ? ' app-sidebar--admin' : ''}`;

  return (
    <aside className={sidebarClass}>
      {/* 品牌区 */}
      <div className="app-sidebar__brand">
        <div className="app-sidebar__logo" aria-hidden="true">
          EDA
        </div>
        <div>
          <div className="app-sidebar__title">Excel 数据分析</div>
          <div className="page-subtitle">{brandSubtitle ?? '智能体工作台'}</div>
        </div>
      </div>

      {/* 导航区 */}
      <nav aria-label={side === 'admin' ? '管理后台导航' : '用户端导航'}>
        <div className="app-sidebar__section">{side === 'admin' ? '管理' : '工作区'}</div>
        <ul>
          {items.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
                end={item.to === '/'}
              >
                <Icon name={item.icon} size={18} />
                <span className="nav-item__label">{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}

export default AppSidebar;
