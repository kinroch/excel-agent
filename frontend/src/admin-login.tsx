/**
 * 入口：admin-login.html（管理端登录）
 *
 * 功能：挂载管理端登录页（无注册入口，仅管理员账号可登录，UI/UX 6.2）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import AuthPage from '@/pages/AuthPage';

import '@/styles/tokens.css';
import '@/styles/base.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 admin-login.html');
}

createRoot(container).render(
  <StrictMode>
    <AuthPage side="admin" />
  </StrictMode>,
);
