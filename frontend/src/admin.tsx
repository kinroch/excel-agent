/**
 * 入口：admin.html（管理端主应用）
 *
 * 功能：挂载管理端应用外壳与路由树（/dashboard、/users、/sessions）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { setApiSide } from '@/api/client';
import AdminApp from '@/router/AdminApp';

import '@/styles/tokens.css';
import '@/styles/base.css';

// 显式声明当前入口所属端：管理端应用内路由为 /dashboard 等（不以 /admin 开头），
// 若不声明，401 拦截器会误判为「用户端」并跳错登录页
setApiSide('admin');

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 admin.html');
}

createRoot(container).render(
  <StrictMode>
    <AdminApp />
  </StrictMode>,
);
