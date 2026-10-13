/**
 * 入口：user.html（用户端主应用）
 *
 * 功能：挂载用户端应用外壳与路由树（/workspace、/chat、/report、/sessions、/stats）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import UserApp from '@/router/UserApp';

import '@/styles/tokens.css';
import '@/styles/base.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 user.html');
}

createRoot(container).render(
  <StrictMode>
    <UserApp />
  </StrictMode>,
);
