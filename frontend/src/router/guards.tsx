/**
 * router/guards.tsx —— 双端路由守卫（依据技术文档 6.3「路由与双端守卫」、UI/UX 3.2 守卫矩阵）
 *
 * 功能说明
 * --------
 * 实现「登录即分流」的四条守卫规则（逐条对照 UI/UX 3.2 表格）：
 *
 * | 场景                          | 行为                                                     |
 * |-------------------------------|----------------------------------------------------------|
 * | 未登录访问 user.html          | → /login.html?from=user，显示「请先登录」                  |
 * | 未登录访问 admin.html         | → /admin-login.html?from=admin，显示「请先登录管理后台」    |
 * | 管理员登录态访问用户端        | → /login.html?denied=1，显示「无权限访问用户端」            |
 * | 普通用户登录态访问管理后台    | → /admin-login.html?denied=1，显示「无权限访问管理后台」    |
 *
 * 关键设计（评审重点）
 * --------------------
 * ① **双保险中的「前端守卫」这一半**：前端守卫负责体验（不闪现受保护内容、
 *    给出可读引导）；**真正的安全边界是后端角色依赖**（api/deps.require_user / require_admin）。
 *    前端守卫可被绕过（改 localStorage、直接调接口），因此绝不能把它当作权限控制的唯一手段。
 *    E2E 用例 C6 专门验证「篡改本地 role」会被守卫拦下，但真正的拦截在后端。
 *
 * ② **判定必须同时看两端登录态**：
 *    两端登录态分属 `eda.auth` 与 `eda.admin.auth` 两个键（互不授权）。
 *    普通用户在**用户端**登录后访问 admin.html 时，「管理端」这个键本来就是空的——
 *    若只看本端键，会把「已登录但越权」误判成「未登录」，从而给出 `?from=admin`
 *    与「请先登录管理后台」，而 UI/UX 3.2 要求的是 `?denied=1` 与「无权限访问管理后台」。
 *    因此这里把「任一端持有登录态」视为已登录，再用**持有登录态那一端的角色**判越权。
 *
 * ③ **同步判定 + 跳转而非渲染**：判定完全基于 localStorage 的同步读取，
 *    因此可以在同一帧内决定跳转并返回 null，不会先渲染受保护内容再跳走（避免闪现）。
 *
 * ④ **服务端复核（useSessionCheck）与「降级容错」**：
 *    本地 role 可被篡改，故通过校验后调用 A4 以令牌换取服务端权威信息并回写 store。
 *    - 服务端判定令牌失效（1003）或账号停用（1004）→ 清除登录态并跳登录页；
 *    - **网络异常等其它错误 → 保留本地登录态**（宁可短暂放行也不把用户无故踢出，
 *      真正的数据安全由后端接口鉴权保证）。
 */

import type { ReactElement, ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';

import { BizApiError, getLoginPage } from '@/api/client';
import { loadAuth, saveAuth, useAdminAuth, useUserAuth } from '@/stores/auth';
import type { AuthSide, AuthState } from '@/stores/auth';

/** RequireAuth 属性 */
export interface RequireAuthProps {
  /** 期望的端：用户端 / 管理端 */
  side: AuthSide;
  /** 受保护内容 */
  children: ReactNode;
}

/**
 * 取另一端的标识。
 *
 * @param side 当前端
 * @returns 另一端
 */
function otherSideOf(side: AuthSide): AuthSide {
  return side === 'admin' ? 'user' : 'admin';
}

/**
 * 依据守卫矩阵判定是否需要跳转。
 *
 * 说明：
 *   不对外导出——保持本文件「只导出组件」以符合 react-refresh 的热更新约束；
 *   若后续需要单测该判定逻辑，应将其抽到独立模块（如 router/guardRules.ts）。
 *
 * @param side 当前端
 * @param ownState 本端登录态
 * @param otherState 另一端登录态
 * @returns 需要跳转时返回目标 URL；通过校验时返回 null
 */
function resolveRedirectUrl(
  side: AuthSide,
  ownState: AuthState,
  otherState: AuthState,
): string | null {
  const loginPage = getLoginPage(side);

  // 规则 1/2：两个键都没有登录态 → 未登录，跳本端登录页并携带 ?from=
  if (!ownState.token && !otherState.token) {
    return `${loginPage}?from=${side}`;
  }

  // 已登录：以「实际持有令牌那一端」的角色判定是否越权
  const effectiveRole = ownState.token ? ownState.role : otherState.role;

  // 规则 3/4：角色与本端不符 → 跳本端登录页并携带 ?denied=1
  if (effectiveRole !== side) {
    return `${loginPage}?denied=1`;
  }

  return null;
}

/**
 * 服务端复核登录态（降级容错见模块头「关键设计 ④」）。
 *
 * @param side 当前端
 * @param enabled 是否启用复核（跳转中时不复核，避免用错误端的令牌发起请求）
 */
function useSessionCheck(side: AuthSide, enabled: boolean): void {
  const userStore = useUserAuth;
  const adminStore = useAdminAuth;

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;

    const check = async (): Promise<void> => {
      try {
        // 用 A4 换取服务端权威用户信息，并回写 store（role 只信服务端）
        const store = side === 'admin' ? adminStore : userStore;
        await store.getState().refresh();
      } catch (error) {
        if (cancelled) return;

        if (error instanceof BizApiError && (error.code === 1003 || error.code === 1004)) {
          // 令牌失效 / 账号停用 → 清除本地登录态并跳登录页
          saveAuth(side, null);
          window.location.replace(`${getLoginPage(side)}?from=${side}`);
          return;
        }
        // 其它错误（网络异常、服务暂不可用）：保留本地登录态，避免把用户无故踢出
        console.warn('[guard] 登录态服务端复核失败，保留本地登录态', error);
      }
    };

    void check();
    return () => {
      cancelled = true;
    };
  }, [side, enabled, userStore, adminStore]);
}

/**
 * 受保护路由包装组件。
 *
 * @param props RequireAuthProps
 * @returns 通过校验时渲染 children；否则返回 null 并在副作用中跳转
 */
export function RequireAuth({ side, children }: RequireAuthProps): ReactElement | null {
  // 同步读取两端登录态：用于首屏「无闪烁」判定（见模块头「关键设计 ②」）
  const [states] = useState(() => ({
    own: loadAuth(side),
    other: loadAuth(otherSideOf(side)),
  }));

  const redirectUrl = resolveRedirectUrl(side, states.own, states.other);

  // StrictMode 下副作用会执行两次，用 ref 保证只跳转一次
  const redirectedRef = useRef(false);

  useEffect(() => {
    if (redirectUrl && !redirectedRef.current) {
      redirectedRef.current = true;
      // 用 replace 而非 href：避免用户点「返回」又回到被拒页面形成死循环
      window.location.replace(redirectUrl);
    }
  }, [redirectUrl]);

  // 登录态复核：仅在校验通过时执行（跳转中无需复核）
  useSessionCheck(side, redirectUrl === null);

  // 判定未通过时返回 null，杜绝受保护内容闪现
  if (redirectUrl) {
    return null;
  }

  return <>{children}</>;
}

export default RequireAuth;
