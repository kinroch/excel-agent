/**
 * 入口：admin.html（管理端主应用）
 *
 * 功能：挂载管理端应用外壳与路由树（/dashboard、/users、/sessions）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import AdminApp from '@/router/AdminApp';

import '@/styles/tokens.css';
import '@/styles/base.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 admin.html');
}

createRoot(container).render(
  <StrictMode>
    <AdminApp />
  </StrictMode>,
);
