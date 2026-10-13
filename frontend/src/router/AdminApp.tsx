/**
 * router/AdminApp.tsx —— 管理端应用外壳与路由树（依据技术文档 6.3、UI/UX 3.4）
 *
 * 功能说明：
 *   1. 组装管理端外壳：左侧导航 + 顶栏 + 内容区；
 *   2. 定义管理端路由树：/dashboard（默认）、/users、/sessions；
 *   3. 管理端与用户端登录态完全隔离（eda.admin.auth vs eda.auth）。
 */

import type { ReactElement } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import AppSidebar, { type NavItemDef } from '@/components/nav/AppSidebar';
import AdminSessions from '@/pages/admin/AdminSessions';
import Dashboard from '@/pages/admin/Dashboard';
import Users from '@/pages/admin/Users';
import { RequireAuth } from '@/router/guards';

/** 管理端导航项（顺序对齐 UI/UX 3.4 导航体系） */
const ADMIN_NAV: NavItemDef[] = [
  { to: '/dashboard', label: '全局看板', icon: 'dashboard' },
  { to: '/users', label: '用户管理', icon: 'users' },
  { to: '/sessions', label: '会话记录', icon: 'sessions' },
];

/**
 * 管理端应用根组件。
 *
 * @returns 管理端应用 JSX
 */
export function AdminApp(): ReactElement {
  return (
    <BrowserRouter>
      <RequireAuth side="admin">
        <div className="app-shell">
          <AppSidebar items={ADMIN_NAV} side="admin" brandSubtitle="管理后台" />

          <div className="app-main">
            <header className="app-topbar">
              <span className="page-subtitle">管理后台 · 全局运营</span>
              <span className="page-subtitle">M0 骨架</span>
            </header>

            <main className="app-content">
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/users" element={<Users />} />
                <Route path="/sessions" element={<AdminSessions />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </Routes>
            </main>
          </div>
        </div>
      </RequireAuth>
    </BrowserRouter>
  );
}

export default AdminApp;
