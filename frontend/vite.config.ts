/**
 * Vite 构建配置（依据技术文档 6.1「工程结构与技术栈」）
 *
 * 功能说明：
 *   1. 配置五个 HTML 入口（index / login / user / admin-login / admin），实现双端多入口 SPA；
 *   2. 开发服务器把 /api 前缀反向代理到后端 127.0.0.1:8000，规避开发期跨域；
 *   3. 产物输出到 dist/，交由 Nginx 托管（技术文档 2.5 部署架构）。
 *
 * 说明：
 *   - 入口使用相对路径字符串，避免在 ESM 配置中依赖 __dirname；
 *   - 生产环境的路由回退（如 /workspace → user.html）由 Nginx 配置完成，
 *     见 deploy/nginx.conf.example。
 */

import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],

  // 路径别名：'@' 指向 src（与 tsconfig.json 的 paths 配置保持一致）
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  // 开发服务器配置
  server: {
    port: 5173,
    host: '127.0.0.1',
    // 接口代理：前端所有 /api 请求转发至后端，与生产 Nginx 行为保持一致
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },

  // 构建配置：多入口
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      input: {
        index: 'index.html', // 交付入口页（选择进入用户端 / 管理端）
        login: 'login.html', // 用户端登录/注册（M1）
        user: 'user.html', // 用户端主应用（工作台/对话/报告/会话/统计）
        adminLogin: 'admin-login.html', // 管理端登录（M1）
        admin: 'admin.html', // 管理端主应用（看板/用户/会话）
      },
    },
  },
});
