/**
 * pages/admin/Dashboard.tsx —— 全局统计看板（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「全局看板 /dashboard」；UI/UX 6.8。
 * 将在 M6「统计与管理后台」里程碑实现（ST2：5 KPI + 双图 + 口径说明）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 全局看板视图。
 *
 * @returns 占位卡片 JSX
 */
export function Dashboard(): ReactElement {
  return (
    <StageNotice
      title="全局看板"
      milestone="M6"
      features="F-4.8-2（全局指标聚合：用户数/任务量/成功率/图表量/满意度）"
      endpoints="ST2"
      acceptance="AC-4.8-2"
      note="落地内容：5 个 KPI 卡（大屏装饰描边 + 四角括号 + 数字辉光）、双图（趋势折线 + 分布柱状）、口径说明。"
    />
  );
}

export default Dashboard;
