/**
 * stores/auth.ts —— 双端登录态存储与认证动作（依据技术文档 6.3「路由与双端守卫」）
 *
 * 功能说明：
 *   1. 以 zustand 管理登录态（令牌、账号、角色），并按端隔离存储键；
 *   2. 用户端键 `eda.auth`、管理端键 `eda.admin.auth`，两端互不授权（技术文档 6.3）；
 *   3. 会话存储不可用时降级为内存态（对齐原型容错语义）；
 *   4. 提供 login / logout / refresh 三个认证动作，封装 A2 / A3 / A4 接口调用。
 *
 * 关键设计（M1 新增）
 * -------------------
 * ① **role 只信服务端**：`refresh()` 用 A4 接口以令牌换回权威的用户信息，
 *    避免仅凭 localStorage 中被篡改的 role 字段做越权判断（UI/UX 3.2 守卫可信来源）。
 *    本地 role 仅用于「首屏无闪烁」的快速判定，真正边界在后端角色依赖。
 * ② **登录成功但角色不符时主动撤销令牌**：A2 为双端共用接口，后端不区分入口，
 *    因此管理员端登录页收到 role=user 的响应时，前端守卫必须拒绝进入，
 *    并把刚签发的令牌交回服务端吊销（否则会留下「已签发却无人持有」的有效凭证）。
 * ③ **登出先请求后清理**：先调用 A3 让服务端把 jti 入黑名单，再清除本地登录态；
 *    即使 A3 因网络失败，也**必须**继续清理本地态（否则用户会卡在无法登出的状态），
 *    因此用 try/finally 包裹，网络异常不影响登出体验。
 */

import { create } from 'zustand';

import { AUTH_STORAGE_KEYS, BizApiError } from '@/api/client';
import * as authApi from '@/api/auth';
import type { AuthUser, LoginPayload } from '@/api/auth';

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
  /** 直接设置登录态并持久化（守卫内部使用） */
  setAuth: (state: AuthState) => void;
  /** 清除登录态（不调用接口，守卫内部使用） */
  clearAuth: () => void;
  /**
   * 调用 A2 登录。
   *
   * @param payload 账号、密码、是否保持登录
   * @returns 登录成功的用户信息（含服务端下发的 role，供调用方做入口校验）
   * @throws BizApiError 1003 / 1004 / 1006 等业务错误
   * @throws AuthSideMismatchError 角色与当前端不符（令牌已在服务端吊销）
   */
  login: (payload: LoginPayload) => Promise<AuthUser>;
  /**
   * 调用 A3 登出并清除本地登录态。
   */
  logout: () => Promise<void>;
  /**
   * 调用 A4 以令牌换取权威用户信息，刷新本地登录态。
   *
   * @throws BizApiError 令牌失效（1003/1004）时抛出，由守卫处理跳转
   */
  refresh: () => Promise<AuthUser>;
}

/**
 * 角色与当前端不符时抛出的错误。
 *
 * 说明：
 *   单独定义类型而非复用 BizApiError，是因为它并非「后端业务错误」（后端不区分入口），
 *   而是**前端守卫的入口约束**（UI/UX 6.2：管理端登录页需提示「该账号为普通用户，
 *   无权访问管理后台」）。调用方可据此给出与 1403 不同的引导文案。
 */
export class AuthSideMismatchError extends Error {
  /** 实际拿到的角色 */
  public readonly actualRole: 'user' | 'admin';

  constructor(actualRole: 'user' | 'admin') {
    super(actualRole === 'user' ? '该账号为普通用户，无权访问管理后台' : '管理员账号无权访问用户端');
    this.name = 'AuthSideMismatchError';
    this.actualRole = actualRole;
  }
}

/**
 * 创建指定端的 zustand store。
 *
 * @param side 'user' 或 'admin'
 * @returns zustand hook
 */
function createAuthStore(side: AuthSide) {
  return create<AuthStore>((set, get) => ({
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

    login: async (payload: LoginPayload) => {
      const result = await authApi.login(payload);
      const user = result.user;

      // 入口校验：A2 为双端共用，需由前端守卫把「从哪个端登录」约束住（UI/UX 6.2 / AC-4.1-4）
      if (user.role !== side) {
        // 令牌已签发但角色不符 → 立即交回服务端吊销，避免留下无人持有的有效凭证
        try {
          await authApi.logout(result.access_token);
        } catch {
          /* 吊销失败不阻断错误提示；该令牌未被持久化，最迟于自然过期时失效 */
        }
        throw new AuthSideMismatchError(user.role);
      }

      get().setAuth({
        token: result.access_token,
        userId: user.id,
        username: user.username,
        role: user.role,
      });
      return user;
    },

    logout: async () => {
      try {
        await authApi.logout();
      } catch (error) {
        // 令牌可能已过期（A3 返回 401）——属预期情况，不阻断本地清理
        if (!(error instanceof BizApiError)) {
          // 网络异常等同样不阻断登出，但保留日志便于排障
          console.warn('[auth] 登出接口调用失败，仍继续清理本地登录态', error);
        }
      } finally {
        get().clearAuth();
      }
    },

    refresh: async () => {
      const user = await authApi.fetchMe();
      get().setAuth({
        token: get().token,
        userId: user.id,
        username: user.username,
        role: user.role,
      });
      return user;
    },
  }));
}

/** 用户端登录态 hook */
export const useUserAuth = createAuthStore('user');

/** 管理端登录态 hook */
export const useAdminAuth = createAuthStore('admin');
