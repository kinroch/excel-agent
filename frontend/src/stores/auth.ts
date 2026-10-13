/**
 * stores/auth.ts —— 双端登录态存储（依据技术文档 6.3「路由与双端守卫」）
 *
 * 功能说明：
 *   1. 以 zustand 管理登录态（令牌、用户名、角色），并按端隔离存储键；
 *   2. 用户端键 `eda.auth`、管理端键 `eda.admin.auth`，两端互不授权（技术文档 6.3）；
 *   3. 会话存储不可用时降级为内存态（对齐原型容错语义）；
 *   4. 真正的登录 / 登出请求由 M1 认证阶段接入（A1~A4 接口），本阶段仅提供状态容器。
 *
 * 阶段边界：M0 提供状态结构与读写方法；M1 增加 login()/logout() 的接口调用与守卫联动。
 */

import { create } from 'zustand';

import { AUTH_STORAGE_KEYS } from '@/api/client';

/** 当前端类型 */
export type AuthSide = 'user' | 'admin';

/** 登录态数据结构 */
export interface AuthState {
  /** 访问令牌（JWT），未登录为 null */
  token: string | null;
  /** 用户 ID */
  userId: number | null;
  /** 账号（手机号 / 邮箱） */
  username: string | null;
  /** 角色 */
  role: 'user' | 'admin' | null;
}

/** 未登录时的初始状态 */
const EMPTY: AuthState = { token: null, userId: null, username: null, role: null };

/**
 * 从 localStorage 读取指定端的登录态。
 *
 * @param side 端标识
 * @returns 解析成功返回 AuthState；键缺失或 JSON 非法时返回空状态（容错降级）
 */
export function loadAuth(side: AuthSide): AuthState {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEYS[side]);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<AuthState>;
    return { ...EMPTY, ...parsed };
  } catch {
    return EMPTY; // 会话存储不可用 → 降级为内存态
  }
}

/**
 * 写入或清除指定端的登录态。
 *
 * @param side 端标识
 * @param state 登录态；传 null 表示登出（清除存储）
 */
export function saveAuth(side: AuthSide, state: AuthState | null): void {
  try {
    if (state === null) {
      localStorage.removeItem(AUTH_STORAGE_KEYS[side]);
    } else {
      localStorage.setItem(AUTH_STORAGE_KEYS[side], JSON.stringify(state));
    }
  } catch {
    /* 存储不可用时静默降级，不影响内存态运行 */
  }
}

/** 登录态 store 的对外接口 */
interface AuthStore extends AuthState {
  /** 当前端 */
  side: AuthSide;
  /** 设置登录态并持久化 */
  setAuth: (state: AuthState) => void;
  /** 清除登录态 */
  clearAuth: () => void;
}

/**
 * 创建指定端的 zustand store。
 *
 * @param side 'user' 或 'admin'
 * @returns zustand hook
 */
function createAuthStore(side: AuthSide) {
  return create<AuthStore>((set) => ({
    ...loadAuth(side),
    side,
    setAuth: (state: AuthState) => {
      saveAuth(side, state);
      set({ ...state });
    },
    clearAuth: () => {
      saveAuth(side, null);
      set({ ...EMPTY });
    },
  }));
}

/** 用户端登录态 hook */
export const useUserAuth = createAuthStore('user');

/** 管理端登录态 hook */
export const useAdminAuth = createAuthStore('admin');
