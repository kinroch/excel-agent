/**
 * 入口：user.html（用户端主应用）
 *
 * 功能：挂载用户端应用外壳与路由树（/workspace、/chat、/report、/sessions、/stats）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { setApiSide } from '@/api/client';
import UserApp from '@/router/UserApp';

import '@/styles/tokens.css';
import '@/styles/base.css';

// 显式声明当前入口所属端：401 拦截器据此清除正确端的登录态并跳对应登录页
// （管理端的应用内路由不以 /admin 开头，仅靠路径推断会判错端，故必须声明）
setApiSide('user');

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 user.html');
}

createRoot(container).render(
  <StrictMode>
    <UserApp />
  </StrictMode>,
);
