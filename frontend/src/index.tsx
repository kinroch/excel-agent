/**
 * 入口：index.html（交付入口页）
 *
 * 功能：挂载入口页组件，并加载全站样式（tokens.css 必须最先加载，作为唯一样式来源）。
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import IndexPage from '@/pages/IndexPage';

import '@/styles/tokens.css';
import '@/styles/base.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('未找到挂载容器 #root，请检查 index.html');
}

createRoot(container).render(
  <StrictMode>
    <IndexPage />
  </StrictMode>,
);
