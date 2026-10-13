/**
 * router/guards.tsx —— 双端路由守卫（依据技术文档 6.3「路由与双端守卫」）
 *
 * 阶段边界说明：
 *   M0 环境与骨架阶段，本文件仅建立守卫组件结构与调用位置（**直接放行**），
 *   目的是让五入口路由树可运行、可评审；真正的鉴权判定在 **M1 认证与权限** 阶段接入。
 *
 * M1 将在此实现（逐条对应技术文档 6.3 守卫矩阵）：
 *   1. 未登录访问 → 重定向对应登录页并携带 ?from=，显示「请先登录」；
 *   2. 越权访问（role 不符）→ 重定向对方登录页携带 ?denied=1，显示「无权限访问」（AC-4.1-4）；
 *   3. 后端令牌校验为准；本地登录态仅作快速判定与降级容错。
 */

import type { ReactElement, ReactNode } from 'react';

/** RequireAuth 属性 */
export interface RequireAuthProps {
  /** 期望的端：用户端 / 管理端 */
  side: 'user' | 'admin';
  /** 受保护内容 */
  children: ReactNode;
}

/**
 * 受保护路由包装组件。
 *
 * @param props RequireAuthProps
 * @returns 受保护内容（M0 直接放行）
 */
export function RequireAuth({ children }: RequireAuthProps): ReactElement {
  // 【M0 骨架期】不做鉴权判定，直接渲染子内容。
  // 【M1 接入点】此处应读取 stores/auth 的登录态并执行守卫矩阵判定。
  return <>{children}</>;
}

export default RequireAuth;
