/**
 * router/UserApp.tsx —— 用户端应用外壳与路由树（依据技术文档 6.3、UI/UX 3.4）
 *
 * 功能说明：
 *   1. 组装用户端外壳：左侧导航 + 顶栏 + 内容区；
 *   2. 定义用户端路由树：/workspace（默认）、/chat、/report、/sessions、/stats；
 *   3. 根路径重定向至 /workspace（组级入口）；
 *   4. 所有子视图经 RequireAuth 包装（M0 直接放行，M1 接入鉴权）。
 *
 * 路由回退说明：
 *   直接访问 /chat 等深层路径时，生产环境由 Nginx 回退到 user.html（见 deploy/nginx.conf.example）。
 */

import type { ReactElement } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import AppSidebar, { type NavItemDef } from '@/components/nav/AppSidebar';
import Chat from '@/pages/user/Chat';
import Report from '@/pages/user/Report';
import Sessions from '@/pages/user/Sessions';
import Stats from '@/pages/user/Stats';
import Workspace from '@/pages/user/Workspace';
import { RequireAuth } from '@/router/guards';

/** 用户端导航项（顺序对齐 UI/UX 3.4 导航体系） */
const USER_NAV: NavItemDef[] = [
  { to: '/workspace', label: '数据工作台', icon: 'workspace' },
  { to: '/chat', label: '对话式分析', icon: 'chat' },
  { to: '/report', label: '分析报告', icon: 'report' },
  { to: '/sessions', label: '会话管理', icon: 'sessions' },
  { to: '/stats', label: '个人统计', icon: 'stats' },
];

/**
 * 用户端应用根组件。
 *
 * @returns 用户端应用 JSX
 */
export function UserApp(): ReactElement {
  return (
    <BrowserRouter>
      <RequireAuth side="user">
        <div className="app-shell">
          <AppSidebar items={USER_NAV} side="user" brandSubtitle="用户端" />

          <div className="app-main">
            {/* 顶栏：M1 接入用户信息与登出，M5 接入会话切换 */}
            <header className="app-topbar">
              <span className="page-subtitle">用户端 · 对话式数据分析</span>
              <span className="page-subtitle">M0 骨架</span>
            </header>

            <main className="app-content">
              <Routes>
                <Route path="/" element={<Navigate to="/workspace" replace />} />
                <Route path="/workspace" element={<Workspace />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/report" element={<Report />} />
                <Route path="/sessions" element={<Sessions />} />
                <Route path="/stats" element={<Stats />} />
                {/* 未匹配路径回退到工作台，避免骨架期出现空白页 */}
                <Route path="*" element={<Navigate to="/workspace" replace />} />
              </Routes>
            </main>
          </div>
        </div>
      </RequireAuth>
    </BrowserRouter>
  );
}

export default UserApp;
