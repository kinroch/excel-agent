/**
 * api/client.ts —— axios 实例与统一响应体解包（依据技术文档 5.2 / 6.1）
 *
 * 功能说明：
 *   1. 创建全站唯一的 axios 实例，baseURL 固定 /api/v1，开发期由 Vite 代理到后端；
 *   2. 请求拦截：自动附加 Bearer JWT（令牌来源见 stores/auth）；
 *   3. 响应拦截：解包统一响应体 {code, message, data}——code 非 0 时抛出业务错误，
 *      调用方只需处理业务错误对象，无需重复判断 code；
 *   4. 401 统一拦截：清除登录态并按当前入口跳转登录页（技术文档 6.3 守卫矩阵）。
 *
 * 阶段边界：M0 仅完成实例与拦截器骨架；具体模块接口封装随各里程碑落地
 * （M1 auth / M2 files / M3 tasks / M4 reports / M5 sessions / M6 stats、admin）。
 */

import axios, { AxiosError, type AxiosInstance, type AxiosResponse } from 'axios';

/** 统一响应体结构（技术文档 5.2） */
export interface ApiEnvelope<T = unknown> {
  code: number;
  message: string;
  data: T | null;
}

/** 业务错误：由响应拦截器在 code !== 0 时抛出 */
export class BizApiError extends Error {
  /** 业务错误码（见技术文档附录 A） */
  public readonly code: number;

  constructor(code: number, message: string) {
    super(message);
    this.name = 'BizApiError';
    this.code = code;
  }
}

/** 令牌读写：按入口区分用户端 / 管理端键名（技术文档 6.3：eda.auth / eda.admin.auth） */
export const AUTH_STORAGE_KEYS = {
  user: 'eda.auth',
  admin: 'eda.admin.auth',
} as const;

/**
 * 判断当前页面属于哪一端，用于 401 时跳转对应登录页。
 *
 * @returns 'admin' 表示管理端入口，'user' 表示用户端入口
 */
function detectSide(): keyof typeof AUTH_STORAGE_KEYS {
  return window.location.pathname.startsWith('/admin') ? 'admin' : 'user';
}

/** 读取当前端的访问令牌（无则返回 null） */
export function readToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEYS[detectSide()]);
    return raw ? (JSON.parse(raw).token as string) : null;
  } catch {
    return null;
  }
}

/** 全站 axios 实例 */
export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

// ---------------- 请求拦截：附加令牌 ----------------
http.interceptors.request.use((config) => {
  const token = readToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ---------------- 响应拦截：解包 + 401 处理 ----------------
http.interceptors.response.use(
  (response: AxiosResponse<ApiEnvelope>) => {
    const body = response.data;
    // 非统一响应体（如文件下载二进制流）直接透传
    if (body == null || typeof body.code !== 'number') {
      return response;
    }
    if (body.code !== 0) {
      throw new BizApiError(body.code, body.message);
    }
    return response;
  },
  (error: AxiosError) => {
    // 401：清除登录态 → 按当前端跳登录页并携带 ?from=
    if (error.response?.status === 401) {
      const side = detectSide();
      localStorage.removeItem(AUTH_STORAGE_KEYS[side]);
      const loginPage = side === 'admin' ? '/admin-login.html' : '/login.html';
      const from = encodeURIComponent(window.location.pathname);
      window.location.href = `${loginPage}?from=${from}`;
    }
    return Promise.reject(error);
  },
);

/**
 * 便捷请求方法：返回解包后的 data，失败抛 BizApiError。
 *
 * @param promise 由 http 发起、已带泛型的请求 Promise
 * @returns 统一响应体中的 data 字段
 */
export async function unwrap<T>(promise: Promise<AxiosResponse<ApiEnvelope<T>>>): Promise<T> {
  const response = await promise;
  return response.data.data as T;
}

export default http;
