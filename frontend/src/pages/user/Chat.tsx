/**
 * pages/user/Chat.tsx —— 对话式分析（M0 骨架占位，核心界面）
 *
 * 对应：实施方案第 6 章「对话式分析 /chat」；UI/UX 6.4 user.html · ai-chat（核心界面）。
 * 将在 M3「多智能体分析链路」与 M4「可视化与报告」里程碑实现。
 */

import type { ReactElement } from 'react';

import StageNotice from '@/components/feedback/StageNotice';

/**
 * 对话式分析视图。
 *
 * @returns 占位卡片 JSX
 */
export function Chat(): ReactElement {
  return (
    <StageNotice
      title="对话式分析"
      milestone="M3 / M4"
      features="F-4.3-1~9（意图解析/计划/沙箱执行/澄清/重试）、F-4.4-1~6（图表匹配与切换）、F-4.6-7（反馈）"
      endpoints="T1 / T3(SSE) / T4 / T5 / T6"
      acceptance="AC-4.3-*、AC-4.4-*、AC-4.6-*"
      note="落地内容：消息流（意图卡→计划卡→重试警示→结论 KPI→图表→明细→取数过程折叠→反馈）、澄清暂停链路、SSE 事件流、免责声明与 AI 标识。"
    />
  );
}

export default Chat;
