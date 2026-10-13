/**
 * pages/user/Stats.tsx —— 个人数据统计（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「个人统计 /stats」；UI/UX 6.7。
 * 将在 M6「统计与管理后台」里程碑实现（ST1：个人统计 + 时间范围 + 口径表）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 个人统计视图。
 *
 * @returns 占位卡片 JSX
 */
export function Stats(): ReactElement {
  return (
    <StageNotice
      title="个人统计"
      milestone="M6"
      features="F-4.8-1、F-4.8-3（个人指标聚合与口径说明）"
      endpoints="ST1"
      acceptance="AC-4.8-1、AC-4.8-3"
      note="落地内容：KPI 条（任务量/成功率/图表量/满意度）、时间范围 Segmented 切换、口径说明表。"
    />
  );
}

export default Stats;
