/**
 * api/auth.ts —— 认证模块接口封装（A1~A4，依据技术文档 5.4）
 *
 * 功能说明：
 *   1. 封装四个认证接口的类型与调用方法，供登录页、守卫与顶栏复用；
 *   2. 全部走 client.ts 的 http 实例，自动获得「统一响应体解包 + 401 拦截」能力；
 *   3. 类型定义与后端 backend/app/schemas/auth.py 一一对应（契约边界）。
 *
 * 接口对照：
 *   A1 register  POST /api/v1/auth/register   F-4.1-1
 *   A2 login     POST /api/v1/auth/login      F-4.1-2
 *   A3 logout    POST /api/v1/auth/logout     F-4.1-2
 *   A4 me        GET  /api/v1/auth/me         F-4.1-3
 */

import { http, unwrap, type ApiEnvelope } from './client';

/** A1 注册请求体 */
export interface RegisterPayload {
  username: string;
  password: string;
}

/** A1 注册响应体 */
export interface RegisterResult {
  id: number;
  username: string;
  role: 'user' | 'admin';
}

/** A2 登录请求体 */
export interface LoginPayload {
  username: string;
  password: string;
  /** 保持登录状态（7 天），对应 F-4.1-3 */
  remember: boolean;
}

/** 用户简要信息 */
export interface AuthUser {
  id: number;
  username: string;
  role: 'user' | 'admin';
  status: 'active' | 'disabled';
}

/** A2 登录响应体 */
export interface LoginResult {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

/**
 * A1 注册。
 *
 * @param payload 账号与密码
 * @returns 新用户的基本信息
 * @throws BizApiError 1001 该账号已注册 / 1002 密码强度不足
 */
export function register(payload: RegisterPayload): Promise<RegisterResult> {
  return unwrap(http.post<ApiEnvelope<RegisterResult>>('/auth/register', payload));
}

/**
 * A2 登录。
 *
 * @param payload 账号、密码与是否保持登录
 * @returns 令牌与用户信息
 * @throws BizApiError 1003 账号或密码错误 / 1004 账号已停用
 */
export function login(payload: LoginPayload): Promise<LoginResult> {
  return unwrap(http.post<ApiEnvelope<LoginResult>>('/auth/login', payload));
}

/**
 * A3 登出（服务端将当前令牌 jti 加入黑名单）。
 *
 * @param explicitToken 可选：显式指定要吊销的令牌。
 *        默认（不传）使用本地登录态中的令牌；传入时用于「登录成功但角色不符」场景——
 *        此时令牌尚未写入本地存储，需把它交回服务端立即吊销，避免签发出「无人使用却仍有效」的凭证。
 * @returns 是否已吊销
 */
export function logout(explicitToken?: string): Promise<{ revoked: boolean }> {
  return unwrap(
    http.post<ApiEnvelope<{ revoked: boolean }>>(
      '/auth/logout',
      undefined,
      explicitToken ? { headers: { Authorization: `Bearer ${explicitToken}` } } : undefined,
    ),
  );
}

/**
 * A4 获取当前登录用户信息（用于刷新页面后校验令牌有效性）。
 *
 * @returns 当前用户信息
 * @throws 令牌无效时由 401 拦截器统一跳转登录页
 */
export function fetchMe(): Promise<AuthUser> {
  return unwrap(http.get<ApiEnvelope<AuthUser>>('/auth/me'));
}
