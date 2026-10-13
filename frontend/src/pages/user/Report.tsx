/**
 * pages/user/Report.tsx —— 分析报告与导出（M0 骨架占位）
 *
 * 对应：实施方案第 6 章「分析报告与导出 /report」；UI/UX 6.5。
 * 将在 M4「可视化与报告」里程碑实现（R1~R5：生成 / 详情 / 历史 / 导出 Markdown / 回写 Excel）。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 分析报告视图。
 *
 * @returns 占位卡片 JSX
 */
export function Report(): ReactElement {
  return (
    <StageNotice
      title="分析报告"
      milestone="M4"
      features="F-4.5-1~6（报告组装/要素完整/导出 Markdown/回写 Excel/报告历史）"
      endpoints="R1~R5"
      acceptance="AC-4.5-1~4"
      note="落地内容：报告要素七件（结论/关键数字/图表/数据来源/生成时间/口径/AI 标识）、meta 四要素、AiBadge、免责声明、Markdown 导出与 Excel 回写。"
    />
  );
}

export default Report;
