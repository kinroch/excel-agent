/**
 * api/queryClient.ts —— React Query 全局客户端（依据技术文档 6.1、6.2）
 *
 * 功能说明：
 *   1. 创建全站唯一的 QueryClient 实例，供「服务端状态」统一管理；
 *   2. 集中设定默认策略，避免各页面各写一套：
 *      - retry: false —— 本项目的失败几乎都是业务错误（2005 无权限、2004 解析失败等），
 *        重试既无意义又会拖慢错误提示；网络级抖动由用户手动重试覆盖；
 *      - refetchOnWindowFocus: false —— 工作台含轮询查询，聚焦时重复拉取会造成
 *        可见的请求抖动（技术文档 6.2 未要求聚焦刷新）；
 *      - staleTime: 0 —— 文件解析状态是高频变化数据，必须每次挂载都重新校验。
 *   3. **解析进度轮询的时间基准（800ms）**由各查询自行声明（见 Workspace.tsx），
 *      不放全局默认值，因为它们只在解析进行中才需要轮询。
 *
 * 说明：
 *   Provider 组件单独放在 router/AppQueryProvider.tsx，不在本文件导出组件——
 *   同一文件同时导出「组件」与「非组件」会触发 react-refresh/only-export-components，
 *   破坏 Vite 的组件热更新（M1 阶段已踩过该规则）。
 */

import { QueryClient } from '@tanstack/react-query';

/** 解析进度轮询间隔（毫秒），技术文档 5.5 F3 规定约 800ms */
export const PARSE_POLL_INTERVAL = 800;

/** 全站唯一的查询客户端实例 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 0,
    },
    mutations: {
      retry: false,
    },
  },
});
