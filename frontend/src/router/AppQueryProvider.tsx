/**
 * router/AppQueryProvider.tsx —— React Query Provider 包装组件
 *
 * 功能说明：
 *   把 QueryClientProvider 抽成可复用组件，使双端（UserApp / AdminApp）都能以
 *   一行包裹接入服务端状态管理，而不必各自 import QueryClient。
 *   放在 router/ 而非 api/ 的原因：它是「应用外壳装配件」的一部分，
 *   与浏览器路由同层级（见 UserApp.tsx 的包裹顺序）。
 *
 * 包裹顺序约定（重要）：
 *   QueryClientProvider 必须在 BrowserRouter **外侧**——路由内的守卫
 *   （guards.tsx）会用 queryClient 做缓存失效，若 Provider 在内侧，
 *   守卫自身拿不到 client。
 */

import type { ReactElement, ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';

import { queryClient } from '@/api/queryClient';

/** AppQueryProvider 属性 */
export interface AppQueryProviderProps {
  /** 子树（通常为 BrowserRouter） */
  children: ReactNode;
}

/**
 * 渲染 React Query Provider。
 *
 * @param props AppQueryProviderProps
 * @returns 包裹了 QueryClientProvider 的子树
 */
export function AppQueryProvider({ children }: AppQueryProviderProps): ReactElement {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

export default AppQueryProvider;
