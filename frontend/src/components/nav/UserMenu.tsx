/**
 * components/nav/UserMenu.tsx —— 顶栏用户菜单（登录态展示 + 登出）
 *
 * 功能说明
 * --------
 * 1. 展示当前登录账号与角色徽标，使两端身份一目了然（AC-4.1-4 的角色区分可视化）；
 * 2. 提供「登出」入口：调用 A3 让服务端吊销令牌（jti 入黑名单），清除本端登录态，
 *    然后返回本端独立登录页（UI/UX 3.2：登出清对应键并返回独立登录页 / AC-4.1-5）。
 *
 * 设计说明（评审重点）
 * --------------------
 * **为什么拆成三个组件**：用户端与管理端使用两个**独立的 zustand hook**
 * （useUserAuth / useAdminAuth），而 React 要求 Hook 的调用顺序稳定，
 * 不能在同一个组件里按 `side` 条件选择 hook。因此这里采用
 * 「外层按 side 分派 → 内层各自调用对应 hook」的写法，既满足 Hooks 规则，
 * 又保证两端的登录态完全隔离、互不串味。
 */

import type { ReactElement } from 'react';

import { Icon } from '@/icons/Icon';
import { useAdminAuth, useUserAuth } from '@/stores/auth';
import type { AuthSide } from '@/stores/auth';

import './UserMenu.css';

/** UserMenu 属性 */
export interface UserMenuProps {
  /** 端标识 */
  side: AuthSide;
}

/** 用户菜单内容属性（两端共用） */
interface UserMenuViewProps {
  /** 账号；未取到时回退为「未登录」 */
  username: string | null;
  /** 角色 */
  role: 'user' | 'admin' | null;
  /** 端标识（决定文案与跳转目标） */
  side: AuthSide;
  /** 登出动作 */
  onLogout: () => void;
}

/** 各端登录页（登出后返回） */
const LOGIN_PAGE: Record<AuthSide, string> = {
  user: '/login.html',
  admin: '/admin-login.html',
};

/**
 * 用户菜单的纯展示层（不含 Hook，便于两端复用）。
 *
 * @param props UserMenuViewProps
 * @returns 菜单 JSX
 */
function UserMenuView({ username, role, side, onLogout }: UserMenuViewProps): ReactElement {
  const displayName = username ?? '未登录';
  // 头像取账号首字符（大写），无账号时用占位符
  const initial = displayName.charAt(0).toUpperCase();

  return (
    <div className="user-menu">
      <span className="user-menu__avatar" aria-hidden="true">
        {initial}
      </span>
      <span className="user-menu__meta">
        <span className="user-menu__name" title={displayName}>
          {displayName}
        </span>
        <span className="user-menu__role">{role === 'admin' ? '管理员' : '普通用户'}</span>
      </span>
      <button
        type="button"
        className="user-menu__logout"
        onClick={() => {
          onLogout();
          // 整页跳转回本端登录页（五入口为独立 HTML 页面）
          window.location.href = LOGIN_PAGE[side];
        }}
      >
        <Icon name="logout" size={16} />
        登出
      </button>
    </div>
  );
}

/**
 * 用户端顶栏菜单。
 *
 * @returns 用户端菜单 JSX
 */
function EndUserMenu(): ReactElement {
  const username = useUserAuth((state) => state.username);
  const role = useUserAuth((state) => state.role);
  const logout = useUserAuth((state) => state.logout);

  return (
    <UserMenuView side="user" username={username} role={role} onLogout={() => void logout()} />
  );
}

/**
 * 管理端顶栏菜单。
 *
 * @returns 管理端菜单 JSX
 */
function AdminMenu(): ReactElement {
  const username = useAdminAuth((state) => state.username);
  const role = useAdminAuth((state) => state.role);
  const logout = useAdminAuth((state) => state.logout);

  return (
    <UserMenuView side="admin" username={username} role={role} onLogout={() => void logout()} />
  );
}

/**
 * 顶栏用户菜单（按端分派到对应 store）。
 *
 * @param props UserMenuProps
 * @returns 菜单 JSX
 */
export function UserMenu({ side }: UserMenuProps): ReactElement {
  return side === 'admin' ? <AdminMenu /> : <EndUserMenu />;
}

export default UserMenu;
