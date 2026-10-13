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

/** 两端的独立登录入口（UI/UX 3.1：登录态隔离，不共用登录页） */
export const LOGIN_PAGES = {
  user: '/login.html',
  admin: '/admin-login.html',
} as const;

/**
 * 取指定端的登录页地址。
 *
 * @param side 'user' 或 'admin'
 * @returns 该端登录页的站点根路径
 */
export function getLoginPage(side: keyof typeof LOGIN_PAGES): string {
  return LOGIN_PAGES[side];
}

/**
 * 当前运行端（由各入口显式声明，见 setApiSide）。
 *
 * 说明：
 *   不能仅靠 `pathname.startsWith('/admin')` 推断端——管理端的应用内路由是
 *   `/dashboard`、`/users`、`/sessions`（UI/UX 3.4），**都不以 /admin 开头**，
 *   路径推断会把管理端的 401 误跳到用户端登录页。因此由入口文件显式声明，
 *   路径推断仅作为未声明时的兜底。
 */
let declaredSide: keyof typeof AUTH_STORAGE_KEYS | null = null;

/**
 * 声明当前入口所属端（在 user.tsx / admin.tsx 挂载前调用一次）。
 *
 * @param side 'user' 或 'admin'
 */
export function setApiSide(side: keyof typeof AUTH_STORAGE_KEYS): void {
  declaredSide = side;
}

/**
 * 判断当前页面属于哪一端，用于 401 时跳转对应登录页。
 *
 * @returns 'admin' 表示管理端入口，'user' 表示用户端入口
 */
function detectSide(): keyof typeof AUTH_STORAGE_KEYS {
  if (declaredSide) {
    return declaredSide;
  }
  // 兜底推断（入口未声明时）：按路径前缀粗判
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

/**
 * 全站 axios 实例。
 *
 * ⚠ 这里**刻意不设置全局 `Content-Type`**，原因是一个真实踩到的坑：
 *   axios 1.x 的 `defaults.transformRequest` 里有这样一条判据——
 *       isFormData(data) ? (hasJSONContentType ? JSON.stringify(formDataToJSON(data)) : data) : ...
 *   其中 `hasJSONContentType` 取自**请求头**。只要实例默认头里带了
 *   `application/json`，上传时塞进去的 `FormData` 就会被先转成 JSON 字符串；
 *   而 `formDataToJSON` 只处理字符串/数组，会**直接丢弃 File / Blob**，
 *   最终发出的请求体是空对象 `{}`，后端必然报
 *   `参数错误[body.file]：Field required`。
 *
 *   症状具有极强的误导性：网络面板里请求确实发出去了、也确实是 POST，
 *   但文件永远传不过去。该缺陷只有在**真实浏览器**里才暴露（M2 E2E 拦截到）。
 *
 * 不设默认头不会影响 JSON 接口：axios 的 transformRequest 遇到普通对象时
 * 会自动补 `Content-Type: application/json`；而 FormData 则原样透传，
 * 交由浏览器写入带 `boundary` 的 `multipart/form-data`。
 */
export const http: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
});

// ---------------- 请求拦截：附加令牌 ----------------
http.interceptors.request.use((config) => {
  // 已显式指定 Authorization 时不再覆盖（用于「登录成功但角色不符 → 主动撤销该令牌」场景）
  if (config.headers.Authorization) {
    return config;
  }
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
    const url = error.config?.url ?? '';
    // 公开认证接口（A1/A2）的 401 是**业务结果**而非「登录态失效」：
    // 附录 A 1003「账号或密码错误」的 HTTP 状态正是 401。若在此处跳转，
    // 登录页将永远无法展示该提示（页面会被刷新掉），破坏 AC-4.1-3。
    const isPublicAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/register');

    // 401（非公开认证接口）：清除登录态 → 按当前端跳登录页并携带 ?from=（UI/UX 3.2）
    if (error.response?.status === 401 && !isPublicAuthEndpoint) {
      const side = detectSide();
      localStorage.removeItem(AUTH_STORAGE_KEYS[side]);
      // 与 guards.tsx 保持同一口径：from 取端标识（user / admin）而非具体路径，
      // 避免两处守卫对「从哪来」的语义各说各话
      window.location.href = `${getLoginPage(side)}?from=${side}`;
    }

    // 关键：非 2xx 响应同样携带统一响应体（技术文档 5.2：HTTP 状态表达类别，
    // 业务码表达具体原因）。若此处直接抛 AxiosError，调用方只能拿到
    // 「Request failed with status code 4xx」这类无信息量的文案，
    // 附录 A 的全部业务提示（账号已注册/密码强度不足/限流/无权访问…）都会丢失。
    // 因此凡是能解析出业务码的响应，一律转换为 BizApiError 再抛出。
    const body = error.response?.data as ApiEnvelope | undefined;
    if (body && typeof body === 'object' && typeof body.code === 'number' && body.code !== 0) {
      return Promise.reject(new BizApiError(body.code, body.message));
    }

    // 无法解析统一响应体（网络中断、超时、二进制流等）：保持原始错误
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
