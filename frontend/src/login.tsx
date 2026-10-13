/**
 * 入口：login.html（用户端登录 / 注册）
 *
 * 功能：挂载用户端登录页（含注册入口）。认证请求在 M1 阶段接入。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import AuthPage from '@/pages/AuthPage';

import '@/styles/tokens.css';
import '@/styles/base.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 login.html');
}

createRoot(container).render(
  <StrictMode>
    <AuthPage side="user" />
  </StrictMode>,
);
